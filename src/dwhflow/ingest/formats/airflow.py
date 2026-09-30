"""Apache Airflow DAG dosyaları (.py) → görevler, görev sırası ve gömülü SQL soyu.

Dosya **asla çalıştırılmaz ya da içe aktarılmaz**; yalnız ``ast`` ile ayrıştırılır ve yalnız
statik olarak bilinen yapılar okunur:

* DAG: ``with DAG("id", …) as dag:``, ``dag = DAG(dag_id=…)`` (+ ``dag=dag`` / ``with dag:``),
  ``@dag`` ile işaretli işlevler. Zamanlama ``schedule``/``schedule_interval`` metninden
  (cron ya da ``@daily`` gibi hazır değer) ``Schedule(cron=…)`` olur; saat/gün alanları sabitse
  başlama saati ve hafta günleri de çıkarılır. Dataset/Asset zamanlaması ``event`` olur.
* Görevler: ``task_id="…"`` ile oluşturulan operatörler (``.partial(…).expand(…)`` dahil),
  ``@task`` / ``@task.<tür>`` işlevlerinin çağrıları (kimlik işlev adı ya da ``task_id``;
  tekrar çağrıda Airflow gibi ``__1`` eki). TaskGroup / ``@task_group`` önekleri Airflow'daki
  gibi uygulanır (``grup.gorev``; ``prefix_group_id=False`` öneksiz).
* Sıra: ``>>``, ``<<`` (listelerle), ``chain``, ``chain_linear``, ``cross_downstream``,
  ``set_upstream``/``set_downstream``, TaskFlow'da XComArg ile veri geçişi ve
  ``ExternalTaskSensor``. TaskGroup ucu, ifadenin yazıldığı andaki kök/yaprak görevlere açılır.
* SQL: ``sql=`` / ``query=`` / ``hql=`` / ``configuration={"query": {"query": …}}`` sabit metinleri
  (liste de olabilir) ``readers._lineage`` ile çözülür; Jinja şablonları (``{{ ds }}``) önce
  ayıklanır. ``.sql`` dosya yolları yalnız not edilir; değişken ve f-string gibi sabit olmayan
  değerler okunmaz (not düşülür).

Süreç adı görev kimliğidir (DAG öneki yok; böylece aynı iş Control-M/AutoSys adlarıyla
birleşir). Aynı dosyadaki iki DAG aynı görev kimliğini kullanıyorsa ikincisi ``dag_id.görev``
olur. Konumlar ``satır N`` biçimindedir (önizleme tek kod bloğudur).
"""

from __future__ import annotations

import ast
import dataclasses
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...model import ProcessKind, Schedule
from .. import preview as _preview
from ..base import DocReport, ModelBuilder, ref
from ..registry import SNIFF_BYTES, FormatSpec, register
from ._common import PLACEHOLDER, analyze_sql, decode, read_capped, sniff_text, strip_bom

EXTRACTOR = "scheduler:airflow"
TOOL = "Airflow"
#: DAG dosyası için üst sınır (bayt); gerçek DAG'lar bunun çok altındadır.
MAX_BYTES = 10 * 1024 * 1024
#: Çözümlenecek en çok deyim (açılan döngüler dahil) ve açılacak en uzun döngü.
MAX_STEPS = 50_000
MAX_LOOP = 500

_WEEK = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_CRON_DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
_PRESETS = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
            "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *", "@quarterly": "0 0 1 */3 *",
            "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"}
_SQL_KWARGS = ("sql", "query", "hql")
#: ``>>``/``<<`` dışındaki ikili işleçler (terimleri yalnız yan etkileri için değerlendirilir).
_OTHER_OPERATORS = (ast.Add, ast.Sub, ast.Mult, ast.MatMult, ast.Div, ast.Mod, ast.Pow, ast.BitOr, ast.BitXor,
                    ast.BitAnd, ast.FloorDiv)

# --------------------------------------------------------------------------- #
# Statik değerler
# --------------------------------------------------------------------------- #


@dataclass(eq=False)
class _Dag:
    dag_id: str
    line: int
    schedule: Schedule | None = None
    schedule_note: str | None = None
    tasks: dict[str, _Task] = field(default_factory=dict)
    edges: list[tuple[_Task, _Task, int]] = field(default_factory=list)


@dataclass(eq=False)
class _Task:
    task_id: str
    dag: _Dag
    operator: str
    kind: ProcessKind
    line: int
    call: ast.Call | None = None
    #: ExternalTaskSensor hedefleri: (dag_id ya da None, task_id)
    external: list[tuple[str | None, str]] = field(default_factory=list)


@dataclass(eq=False)
class _XCom:
    """Bir görevin çıktısı (TaskFlow çağrısı, ``op.output``); başka görevin argümanı olursa sıra kurar."""

    task: _Task


@dataclass(eq=False)
class _Group:
    own_id: str
    dag: _Dag | None
    parent: _Group | None
    prefix: bool = True
    members: list[_Task] = field(default_factory=list)

    @property
    def full_id(self) -> str:
        return self.parent.child_id(self.own_id) if self.parent else self.own_id

    def child_id(self, label: str) -> str:
        return f"{self.full_id}.{label}" if self.prefix and self.full_id else label


@dataclass(eq=False)
class _TaskFunc:
    task_id: str
    kind: ProcessKind
    operator: str
    line: int


@dataclass(eq=False)
class _GroupFunc:
    node: ast.FunctionDef | ast.AsyncFunctionDef
    group_id: str
    prefix: bool
    scope: _Scope


@dataclass(eq=False)
class _DagFunc:
    dag: _Dag


@dataclass(eq=False)
class _Container:
    """Sözlük gibi uç olamayan ama içinde XComArg taşıyabilen değer."""

    values: list[Any]


class _Scope:
    def __init__(self, parent: _Scope | None) -> None:
        self.vars: dict[str, Any] = {}
        self.parent = parent

    def get(self, name: str) -> Any:
        scope: _Scope | None = self
        while scope is not None:
            if name in scope.vars:
                return scope.vars[name]
            scope = scope.parent
        return None

    def set(self, name: str, value: Any) -> None:
        self.vars[name] = value


@dataclass
class _Ctx:
    dag: _Dag | None
    group: _Group | None
    scope: _Scope


class _TooComplex(Exception):
    pass


# --------------------------------------------------------------------------- #
# Operatör türleri ve zamanlama
# --------------------------------------------------------------------------- #

_DB_WORDS = re.compile(r"sql|query|hql|postgres|mysql|oracle|snowflake|bigquery|redshift|teradata|jdbc|odbc|"
                       r"sqlite|trino|presto|vertica|exasol|hive|impala|clickhouse|db2|athena|synapse|greenplum")
_TRANSFER = re.compile(r"(?<=[A-Za-z0-9])To(?=[A-Z])")


def _operator_kind(name: str) -> ProcessKind:
    lowered = name.lower()
    if "sensor" in lowered:
        return ProcessKind.BATCH_JOB
    transfer = _TRANSFER.search(name)
    if transfer and lowered.endswith("operator"):
        target = name[transfer.end():].lower()
        return ProcessKind.LOADER_UTILITY if _DB_WORDS.search(target) else ProcessKind.FILE_TRANSFER
    if "check" in lowered:
        return ProcessKind.DQ_CHECK
    if "storedprocedure" in lowered:
        return ProcessKind.STORED_PROCEDURE
    if re.search(r"sftp|ftp|scp|filetransfer|copyobject", lowered):
        return ProcessKind.FILE_TRANSFER
    if re.search(r"bash|ssh|winrm|shell", lowered):
        return ProcessKind.SHELL_SCRIPT
    if _DB_WORDS.search(lowered):
        return ProcessKind.SQL_SCRIPT
    return ProcessKind.BATCH_JOB


def _taskflow_kind(variant: str | None) -> ProcessKind:
    if variant == "bash":
        return ProcessKind.SHELL_SCRIPT
    if variant == "sql":
        return ProcessKind.SQL_SCRIPT
    return ProcessKind.BATCH_JOB


def _cron_value(token: str) -> int | None:
    if token.isdigit() and int(token) <= 7:
        return int(token)
    return _CRON_DAYS.get(token.lower()[:3]) if token.isalpha() and len(token) == 3 else None


def _cron_days(value: str) -> list[str]:
    if value in ("*", "?"):
        return []
    days: set[int] = set()
    for part in value.split(","):
        match = re.fullmatch(r"(\w+)(?:-(\w+))?", part)
        if not match:
            return []
        first = _cron_value(match.group(1))
        last = _cron_value(match.group(2)) if match.group(2) else first
        if first is None or last is None or first > last:
            return []
        days.update(number % 7 for number in range(first, last + 1))
    return [day for day in _WEEK if _CRON_DAYS[day] in days]


def _cron_schedule(text: str) -> tuple[Schedule | None, str | None]:
    """Cron/hazır değer → Schedule. Çözülemeyen hazır değerler (``@once``) yalnız not olur."""
    expression = _PRESETS.get(text.lower(), text) if text.startswith("@") else text
    fields = expression.split()
    if len(fields) not in (5, 6):
        return None, f"zamanlama {text}"
    minute, hour = fields[0], fields[1]
    times: list[str] = []
    number_list = r"\d{1,2}(?:,\d{1,2})*"
    if re.fullmatch(number_list, minute) and re.fullmatch(number_list, hour):
        pairs = [(int(h), int(m)) for h in hour.split(",") for m in minute.split(",")]
        if len(pairs) <= 48 and all(h < 24 and m < 60 for h, m in pairs):
            times = sorted({f"{h:02d}:{m:02d}" for h, m in pairs})
    return Schedule(cron=text, start_times=times, days_of_week=_cron_days(fields[4])), None


# --------------------------------------------------------------------------- #
# Çözümleyici
# --------------------------------------------------------------------------- #


class _Analyzer:
    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.aliases: dict[str, str] = {}
        self.dags: list[_Dag] = []
        self.notes: list[str] = []
        self.module = _Scope(None)
        self.steps = 0
        self.active: set[int] = set()
        self.upstream: dict[_Task, set[_Task]] = {}
        self.downstream: dict[_Task, set[_Task]] = {}

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    def run(self) -> None:
        self._block(self.tree.body, _Ctx(None, None, self.module))

    # -- yardımcılar -------------------------------------------------------- #

    def _qual(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self._qual(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return ""

    def _last(self, node: ast.AST) -> str:
        return self._qual(node).rsplit(".", 1)[-1]

    @staticmethod
    def _kwargs(call: ast.Call | None) -> dict[str, ast.expr]:
        return {k.arg: k.value for k in call.keywords if k.arg} if call is not None else {}

    def _literal_str(self, node: ast.AST | None) -> str | None:
        """Sabit metin (bitişik/``+`` birleştirme, yer tutucusuz f-string, ``dedent("…")`` dahil)."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            texts = [self._literal_str(part) for part in self._left_chain(node, (ast.Add,))]
            if any(text is None for text in texts):
                return None
            return "".join(text for text in texts if text is not None)
        if isinstance(node, ast.JoinedStr) and all(isinstance(v, ast.Constant) for v in node.values):
            return "".join(str(v.value) for v in node.values)
        if (isinstance(node, ast.Call) and self._last(node.func) in ("dedent", "cleandoc")
                and len(node.args) == 1 and not node.keywords):
            inner = self._literal_str(node.args[0])
            return textwrap.dedent(inner) if inner is not None else None
        return None

    @staticmethod
    def _left_chain(node: ast.BinOp, operators: tuple[type[ast.operator], ...]) -> list[ast.expr]:
        """``a + b + c`` gibi sola yaslı zincirin terimleri, soldan sağa (derin özyineleme olmadan)."""
        parts: list[ast.expr] = []
        current: ast.expr = node
        while isinstance(current, ast.BinOp) and isinstance(current.op, operators):
            parts.append(current.right)
            current = current.left
        parts.append(current)
        parts.reverse()
        return parts

    def _literal_bool(self, node: ast.AST | None, default: bool) -> bool:
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, bool) else default

    # -- deyimler ----------------------------------------------------------- #

    def _block(self, statements: list[ast.stmt], ctx: _Ctx) -> None:
        for statement in statements:
            self._stmt(statement, ctx)

    def _stmt(self, stmt: ast.stmt, ctx: _Ctx) -> None:
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise _TooComplex
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                local = alias.asname or alias.name.split(".")[0]
                self.aliases[local] = alias.name if alias.asname else local
        elif isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                full = f"{stmt.module}.{alias.name}" if stmt.module else alias.name
                self.aliases[alias.asname or alias.name] = full
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            inner = ctx
            for item in stmt.items:
                value = self._eval(item.context_expr, inner)
                if isinstance(value, _Dag):
                    inner = _Ctx(value, None, inner.scope)
                elif isinstance(value, _Group):
                    inner = _Ctx(value.dag or inner.dag, value, inner.scope)
                if item.optional_vars is not None:
                    self._bind(item.optional_vars, value, inner)
            self._block(stmt.body, inner)
        elif isinstance(stmt, ast.Assign):
            value = self._eval(stmt.value, ctx)
            for target in stmt.targets:
                self._bind(target, value, ctx)
        elif isinstance(stmt, ast.AnnAssign):
            if stmt.value is not None:
                self._bind(stmt.target, self._eval(stmt.value, ctx), ctx)
        elif isinstance(stmt, ast.AugAssign):
            self._eval(stmt.value, ctx)
        elif isinstance(stmt, ast.Expr):
            self._eval(stmt.value, ctx)
        elif isinstance(stmt, ast.Return):
            if stmt.value is not None:
                self._eval(stmt.value, ctx)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._function(stmt, ctx)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            self._for(stmt, ctx)
        elif isinstance(stmt, (ast.While, ast.If)):
            self._block(stmt.body, ctx)
            self._block(stmt.orelse, ctx)
        elif isinstance(stmt, (ast.Try, ast.TryStar)):
            self._block(stmt.body, ctx)
            for handler in stmt.handlers:
                self._block(handler.body, ctx)
            self._block(stmt.orelse, ctx)
            self._block(stmt.finalbody, ctx)
        elif isinstance(stmt, ast.Match):
            for case in stmt.cases:
                self._block(case.body, ctx)

    def _bind(self, target: ast.AST, value: Any, ctx: _Ctx) -> None:
        if isinstance(target, ast.Name):
            ctx.scope.set(target.id, value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            values = value if isinstance(value, list) and len(value) == len(target.elts) else [None] * len(
                target.elts)
            for element, item in zip(target.elts, values, strict=True):
                self._bind(element, item, ctx)

    def _decorator(self, stmt: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str | None, ast.Call | None,
                                                                                   str | None, int]:
        """(tür: dag | task | task_group | None, dekoratör çağrısı, @task.<tür>, satır).

        ``@task.run_if``/``@task.skip_if`` yalnız koşul ekler; görevin türü diğer @task dekoratöründen gelir.
        """
        line = min((d.lineno for d in stmt.decorator_list), default=stmt.lineno)
        tasks: list[tuple[ast.Call | None, str | None]] = []
        for decorator in stmt.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            parts = self._qual(call.func if call else decorator).split(".")
            if parts[-1] in ("dag", "task_group"):
                return parts[-1], call, None, line
            if parts[-1] == "task":
                tasks.append((call, None))
            elif len(parts) >= 2 and parts[-2] == "task" and parts[-1] not in ("run_if", "skip_if"):
                tasks.append((call, parts[-1]))
        if tasks:
            return "task", tasks[0][0], tasks[0][1], line
        return None, None, None, line

    def _function(self, stmt: ast.FunctionDef | ast.AsyncFunctionDef, ctx: _Ctx) -> None:
        kind, call, variant, line = self._decorator(stmt)
        kwargs = self._kwargs(call)
        if kind == "dag":
            dag_id = (self._literal_str(kwargs.get("dag_id")) or "").strip() or stmt.name
            dag = self._new_dag(dag_id, kwargs, line)
            scope = _Scope(ctx.scope)
            for arg in [*stmt.args.posonlyargs, *stmt.args.args, *stmt.args.kwonlyargs]:
                scope.set(arg.arg, None)  # DAG parametreleri: dıştaki aynı adlı değerleri gölgeler
            self._block(stmt.body, _Ctx(dag, None, scope))
            ctx.scope.set(stmt.name, _DagFunc(dag))
        elif kind == "task":
            task_id = (self._literal_str(kwargs.get("task_id")) or "").strip() or stmt.name
            label = f"@task.{variant}" if variant else "@task"
            ctx.scope.set(stmt.name, _TaskFunc(task_id, _taskflow_kind(variant), label, line))
        elif kind == "task_group":
            group_id = (self._literal_str(kwargs.get("group_id")) or "").strip() or stmt.name
            prefix = self._literal_bool(kwargs.get("prefix_group_id"), True)
            ctx.scope.set(stmt.name, _GroupFunc(stmt, group_id, prefix, ctx.scope))
        else:
            ctx.scope.set(stmt.name, None)

    def _for(self, stmt: ast.For | ast.AsyncFor, ctx: _Ctx) -> None:
        items = self._eval(stmt.iter, ctx)
        if (isinstance(items, list) and len(items) <= MAX_LOOP
                and any(isinstance(item, (_Task, _XCom, _Group, list)) for item in items)):
            for item in items:
                self._bind(stmt.target, item, ctx)
                self._block(stmt.body, ctx)
        else:
            if self._dynamic_body(stmt.body):
                self.note(f"satır {stmt.lineno}: döngü statik olarak açılamadı; içindeki görevler yalnız sabit "
                          "kimlikleriyle, bağımlılıklar çözülebildiği kadar okundu")
            self._bind(stmt.target, None, ctx)
            self._block(stmt.body, ctx)
        self._block(stmt.orelse, ctx)

    def _dynamic_body(self, body: list[ast.stmt]) -> bool:
        for node in (n for statement in body for n in ast.walk(statement)):
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.RShift, ast.LShift)):
                return True
            if isinstance(node, ast.Call):
                task_id = self._kwargs(node).get("task_id")
                if task_id is not None and self._literal_str(task_id) is None:
                    return True
                if self._last(node.func) in ("chain", "chain_linear", "cross_downstream", "set_downstream",
                                             "set_upstream"):
                    return True
        return False

    # -- ifadeler ----------------------------------------------------------- #

    def _eval(self, node: ast.AST | None, ctx: _Ctx) -> Any:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return ctx.scope.get(node.id)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            values: list[Any] = []
            for element in node.elts:
                value = self._eval(element.value if isinstance(element, ast.Starred) else element, ctx)
                if isinstance(element, ast.Starred) and isinstance(value, list):
                    values.extend(value)
                else:
                    values.append(value)
            return values
        if isinstance(node, ast.Dict):
            return _Container([self._eval(v, ctx) for v in node.values if v is not None])
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, (ast.RShift, ast.LShift)):
                return self._shift_chain(node, ctx)
            for part in self._left_chain(node, _OTHER_OPERATORS):
                self._eval(part, ctx)  # yan etkiler (iç içe görev oluşturma) için
            return None
        if isinstance(node, ast.Call):
            return self._call(node, ctx)
        if isinstance(node, ast.Attribute):
            base = self._eval(node.value, ctx)
            if node.attr == "output" and isinstance(base, _Task):
                return _XCom(base)
            return None
        if isinstance(node, ast.Subscript):
            base = self._eval(node.value, ctx)
            if isinstance(base, _XCom):
                return base
            if (isinstance(base, list) and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, int) and -len(base) <= node.slice.value < len(base)):
                return base[node.slice.value]
            return None
        if isinstance(node, ast.NamedExpr):
            value = self._eval(node.value, ctx)
            self._bind(node.target, value, ctx)
            return value
        if isinstance(node, ast.Await):
            return self._eval(node.value, ctx)
        if isinstance(node, ast.Starred):
            return self._eval(node.value, ctx)
        return None

    def _shift_chain(self, node: ast.BinOp, ctx: _Ctx) -> Any:
        """``a >> b << c …`` zinciri, derin özyinelemeye girmeden soldan sağa (Airflow sırasıyla)."""
        operators: list[ast.operator] = []
        operands: list[ast.expr] = []
        current: ast.expr = node
        while isinstance(current, ast.BinOp) and isinstance(current.op, (ast.RShift, ast.LShift)):
            operators.append(current.op)
            operands.append(current.right)
            current = current.left
        operands.append(current)
        operators.reverse()
        operands.reverse()
        left = self._eval(operands[0], ctx)
        for operator, operand in zip(operators, operands[1:], strict=True):
            right = self._eval(operand, ctx)
            if isinstance(operator, ast.RShift):
                self._depend(left, right, operand.lineno)
            else:
                self._depend(right, left, operand.lineno)
            left = right
        return left

    def _call(self, node: ast.Call, ctx: _Ctx) -> Any:
        func = node.func
        last = self._last(func)
        attribute = func.attr if isinstance(func, ast.Attribute) else None
        arguments = [*node.args, *(k.value for k in node.keywords)]
        if isinstance(func, ast.Attribute) and attribute in ("set_downstream", "set_upstream"):
            base = self._eval(func.value, ctx)
            for other in [self._eval(arg, ctx) for arg in node.args]:
                if attribute == "set_downstream":
                    self._depend(base, other, node.lineno)
                else:
                    self._depend(other, base, node.lineno)
            return None
        if last in ("chain", "chain_linear", "cross_downstream"):
            args: list[Any] = []
            for arg in node.args:
                value = self._eval(arg.value if isinstance(arg, ast.Starred) else arg, ctx)
                args.extend(value if isinstance(arg, ast.Starred) and isinstance(value, list) else [value])
            self._helper(last, args, node.lineno)
            return None
        if isinstance(func, ast.Attribute) and attribute in ("expand", "expand_kwargs", "partial", "override"):
            # Taban bir kez değerlendirilir (iki kez değerlendirmek görevleri iki kez kaydederdi).
            base = self._eval(func.value, ctx)
            if isinstance(base, _TaskFunc):
                if attribute == "override":
                    task_id = (self._literal_str(self._kwargs(node).get("task_id")) or "").strip()
                    return dataclasses.replace(base, task_id=task_id) if task_id else base
                if attribute == "partial":
                    for value in arguments:
                        self._eval(value, ctx)
                    return base
                return self._taskflow(base, node, ctx)
            if isinstance(base, _GroupFunc) and attribute == "override":
                group_id = (self._literal_str(self._kwargs(node).get("group_id")) or "").strip()
                return dataclasses.replace(base, group_id=group_id) if group_id else base
            if attribute != "partial":
                target = base.task if isinstance(base, _XCom) else base
                for value in arguments:
                    for up in self._xcoms(self._eval(value, ctx)):
                        if isinstance(target, _Task):
                            self._edge(up, target, node.lineno)
                return base if isinstance(base, (_Task, _XCom)) else None
            # ``Operator.partial(task_id=…)``: aşağıdaki operatör yolu işler (taban bir sınıf adıdır).
        if last == "DAG":
            kwargs = self._kwargs(node)
            dag_id = self._literal_str(node.args[0] if node.args else kwargs.get("dag_id"))
            return self._new_dag(dag_id, kwargs, node.lineno)
        if last == "TaskGroup":
            return self._task_group(node, ctx)
        kwargs = self._kwargs(node)
        if "task_id" in kwargs:
            return self._operator(node, kwargs, ctx)
        callee = self._eval(func, ctx) if isinstance(func, (ast.Name, ast.Attribute, ast.Call)) else None
        if isinstance(callee, _TaskFunc):
            return self._taskflow(callee, node, ctx)
        if isinstance(callee, _GroupFunc):
            return self._group_call(callee, node, ctx)
        if isinstance(callee, _DagFunc):
            return callee.dag
        for value in [*node.args, *(k.value for k in node.keywords)]:
            self._eval(value, ctx)
        return None

    # -- DAG, grup, görev ---------------------------------------------------- #

    def _new_dag(self, dag_id: str | None, kwargs: dict[str, ast.expr], line: int) -> _Dag:
        if dag_id is None or not dag_id.strip():
            self.note(f"satır {line}: dag_id sabit metin değil")
            dag_id = f"(satır {line}'deki DAG)"
        elif any(d.dag_id == dag_id for d in self.dags):
            self.note(f"satır {line}: '{dag_id}' DAG kimliği dosyada iki kez kullanılmış")
        schedule, note = self._schedule(kwargs)
        dag = _Dag(dag_id, line, schedule, note)
        self.dags.append(dag)
        return dag

    def _schedule(self, kwargs: dict[str, ast.expr]) -> tuple[Schedule | None, str | None]:
        node = kwargs.get("schedule", kwargs.get("schedule_interval"))
        if node is None:
            return (None, "zamanlama timetable ile (statik çözülemedi)") if "timetable" in kwargs else (None, None)
        if isinstance(node, ast.Constant) and node.value is None:
            return None, "zamanlama yok (elle ya da dış tetikleme)"
        text = self._literal_str(node)
        if text is not None and text.strip():
            return _cron_schedule(text.strip())
        elements = node.elts if isinstance(node, (ast.List, ast.Tuple)) else [node]
        uris = [self._literal_str(e.args[0]) for e in elements
                if isinstance(e, ast.Call) and self._last(e.func) in ("Dataset", "Asset") and e.args]
        uris = [uri for uri in uris if uri]
        if uris:
            return Schedule(event="Airflow dataset: " + ", ".join(uris)), None
        return None, "zamanlama statik çözülemedi"

    def _task_group(self, node: ast.Call, ctx: _Ctx) -> _Group:
        kwargs = self._kwargs(node)
        group_id = self._literal_str(node.args[0] if node.args else kwargs.get("group_id"))
        prefix = self._literal_bool(kwargs.get("prefix_group_id"), True)
        if group_id is None or not group_id.strip():
            self.note(f"satır {node.lineno}: TaskGroup kimliği sabit metin değil; görevler öneksiz adlandırıldı")
            group_id, prefix = "", False
        parent = self._eval(kwargs.get("parent_group"), ctx) if "parent_group" in kwargs else ctx.group
        dag = self._eval(kwargs.get("dag"), ctx) if "dag" in kwargs else ctx.dag
        parent = parent if isinstance(parent, _Group) else None
        return _Group(group_id, dag if isinstance(dag, _Dag) else ctx.dag, parent, prefix)

    def _group_call(self, function: _GroupFunc, node: ast.Call, ctx: _Ctx) -> Any:
        if id(function) in self.active:
            self.note(f"satır {node.lineno}: özyinelemeli @task_group çağrısı atlandı")
            return None
        group = _Group(function.group_id, ctx.dag, ctx.group, function.prefix)
        scope = _Scope(function.scope)
        params = [a.arg for a in [*function.node.args.posonlyargs, *function.node.args.args]]
        for name, arg in zip(params, node.args, strict=False):
            scope.set(name, self._eval(arg, ctx))
        for keyword in node.keywords:
            if keyword.arg:
                scope.set(keyword.arg, self._eval(keyword.value, ctx))
        inner = _Ctx(ctx.dag, group, scope)
        result = None
        self.active.add(id(function))
        try:
            for statement in function.node.body:
                if isinstance(statement, ast.Return):
                    result = self._eval(statement.value, inner)
                    break
                self._stmt(statement, inner)
        finally:
            self.active.discard(id(function))
        return result if self._endpoints(result, "up") else group

    def _register(self, task_id: str, dag: _Dag, group: _Group | None, operator: str, kind: ProcessKind,
                  line: int, call: ast.Call | None, *, unique: bool) -> _Task:
        full = group.child_id(task_id) if group is not None else task_id
        if unique and full in dag.tasks:
            core = re.split(r"__\d+$", full)[0]
            numbers = [int(m.group(1)) for other in dag.tasks
                       if (m := re.fullmatch(re.escape(core) + r"__(\d+)", other))]
            full = f"{core}__{max(numbers) + 1 if numbers else 1}"
        elif full in dag.tasks:
            self.note(f"satır {line}: '{full}' görev kimliği DAG'da iki kez tanımlı; ilki kullanıldı")
            return dag.tasks[full]
        task = _Task(full, dag, operator, kind, line, call)
        dag.tasks[full] = task
        self.upstream[task], self.downstream[task] = set(), set()
        owner = group
        while owner is not None:
            owner.members.append(task)
            owner = owner.parent
        return task

    def _operator(self, node: ast.Call, kwargs: dict[str, ast.expr], ctx: _Ctx) -> _Task | None:
        upstream: list[_Task] = []
        for key, value in kwargs.items():
            if key not in ("task_id", "dag", "task_group"):
                upstream.extend(self._xcoms(self._eval(value, ctx)))
        for arg in node.args:
            upstream.extend(self._xcoms(self._eval(arg, ctx)))
        task_id = self._literal_str(kwargs["task_id"])
        if task_id is None or not task_id.strip():
            self.note(f"satır {node.lineno}: task_id sabit (boş olmayan) metin değil; görev atlandı")
            return None
        task_id = task_id.strip()
        func = node.func
        operator = self._last(func.value if isinstance(func, ast.Attribute) and func.attr == "partial" else func)
        dag, group = ctx.dag, ctx.group
        if "dag" in kwargs:
            value = self._eval(kwargs["dag"], ctx)
            if isinstance(value, _Dag) and value is not dag:
                dag, group = value, None
        if "task_group" in kwargs:
            value = self._eval(kwargs["task_group"], ctx)
            if isinstance(value, _Group):
                group, dag = value, value.dag or dag
        if dag is None:
            self.note(f"satır {node.lineno}: '{task_id}' görevi bir DAG'a bağlı değil; atlandı")
            return None
        task = self._register(task_id, dag, group, operator or "Operator", _operator_kind(operator), node.lineno,
                              node, unique=False)
        for up in upstream:
            self._edge(up, task, node.lineno)
        if operator.endswith("ExternalTaskSensor"):
            self._external(task, kwargs, node.lineno)
        return task

    def _external(self, task: _Task, kwargs: dict[str, ast.expr], line: int) -> None:
        dag_id = self._literal_str(kwargs.get("external_dag_id"))
        targets = [self._literal_str(kwargs.get("external_task_id"))]
        if isinstance(kwargs.get("external_task_ids"), (ast.List, ast.Tuple)):
            targets += [self._literal_str(e) for e in kwargs["external_task_ids"].elts]
        targets = [t for t in targets if t]
        if targets:
            task.external.extend((dag_id, target) for target in targets)
        else:
            self.note(f"satır {line}: ExternalTaskSensor belirli bir görevi değil DAG'ın tamamını ya da çözülemeyen "
                      "bir görevi bekliyor; sıra kenarı eklenmedi")

    def _taskflow(self, function: _TaskFunc, node: ast.Call, ctx: _Ctx) -> _XCom | None:
        upstream: list[_Task] = []
        for value in [*node.args, *(k.value for k in node.keywords)]:
            upstream.extend(self._xcoms(self._eval(value, ctx)))
        if ctx.dag is None:
            self.note(f"satır {node.lineno}: '{function.task_id}' görevi bir DAG içinde çağrılmamış; atlandı")
            return None
        task = self._register(function.task_id, ctx.dag, ctx.group, function.operator, function.kind,
                              function.line, None, unique=True)
        for up in upstream:
            self._edge(up, task, node.lineno)
        return _XCom(task)

    # -- bağımlılıklar ------------------------------------------------------ #

    def _xcoms(self, value: Any) -> list[_Task]:
        if isinstance(value, _XCom):
            return [value.task]
        if isinstance(value, list):
            return [task for item in value for task in self._xcoms(item)]
        if isinstance(value, _Container):
            return [task for item in value.values for task in self._xcoms(item)]
        return []

    def _endpoints(self, value: Any, side: str) -> list[_Task]:
        if isinstance(value, _Task):
            return [value]
        if isinstance(value, _XCom):
            return [value.task]
        if isinstance(value, _Group):
            members = set(value.members)
            links = self.upstream if side == "down" else self.downstream
            return [m for m in value.members if not (links[m] & members)]
        if isinstance(value, list):
            return [task for item in value for task in self._endpoints(item, side)]
        return []

    def _depend(self, up: Any, down: Any, line: int) -> None:
        if isinstance(up, list) and isinstance(down, list):
            if self._endpoints(up, "up") and self._endpoints(down, "down"):
                self.note(f"satır {line}: liste >> liste Airflow'da geçersiz; bağımlılık atlandı")
            return
        ups, downs = self._endpoints(up, "up"), self._endpoints(down, "down")
        if not (ups and downs):
            if ups or downs:
                self.note(f"satır {line}: bağımlılığın bir ucu statik olarak çözülemedi; atlandı")
            return
        for first in ups:
            for second in downs:
                self._edge(first, second, line)

    def _edge(self, first: _Task, second: _Task, line: int) -> None:
        if first is second or second in self.downstream[first]:
            return
        if first.dag is not second.dag:
            self.note(f"satır {line}: farklı DAG'lardaki görevler arasında bağımlılık atlandı")
            return
        first.dag.edges.append((first, second, line))
        self.downstream[first].add(second)
        self.upstream[second].add(first)

    def _helper(self, name: str, args: list[Any], line: int) -> None:
        if name == "cross_downstream":
            if len(args) >= 2:
                for first in self._endpoints(args[0], "up"):
                    self._depend(first, args[1], line)
            return
        for up, down in zip(args, args[1:], strict=False):
            if name == "chain" and isinstance(up, list) and isinstance(down, list):
                if len(up) != len(down):
                    self.note(f"satır {line}: chain() içinde farklı uzunlukta listeler; bağımlılık atlandı")
                    continue
                for first, second in zip(up, down, strict=True):
                    self._depend(first, second, line)
            elif isinstance(up, list):
                for first in up:
                    self._depend(first, down, line)
            else:
                self._depend(up, down, line)

    # -- SQL ---------------------------------------------------------------- #

    def task_sql(self, task: _Task) -> tuple[list[str], list[str], int]:
        """(SQL metinleri, .sql dosya yolları, sabit olmayan değer sayısı)."""
        kwargs = self._kwargs(task.call)
        nodes = [kwargs[key] for key in _SQL_KWARGS if key in kwargs]
        configuration = kwargs.get("configuration")
        query = self._dict_get(self._dict_get(configuration, "query"), "query")
        if query is not None:
            nodes.append(query)
        texts, files, dynamic = [], [], 0
        for node in nodes:
            elements = node.elts if isinstance(node, (ast.List, ast.Tuple)) else [node]
            for element in elements:
                if isinstance(element, ast.Constant) and element.value is None:
                    continue
                value = self._literal_str(element)
                if value is None:
                    dynamic += 1
                elif re.fullmatch(r"[^\s;]+\.(?:sql|hql|bteq|btq)", value.strip(), re.I):
                    files.append(value.strip())
                elif value.strip():
                    texts.append(value)
        return texts, files, dynamic

    def _dict_get(self, node: ast.AST | None, key: str) -> ast.expr | None:
        if not isinstance(node, ast.Dict):
            return None
        for name, value in zip(node.keys, node.values, strict=True):
            if self._literal_str(name) == key:
                return value
        return None


# --------------------------------------------------------------------------- #
# Jinja
# --------------------------------------------------------------------------- #

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_JINJA_STATEMENT = re.compile(r"\{%.*?%\}", re.S)
_JINJA_SCHEMA = re.compile(r"\{\{.*?\}\}\s*\.", re.S)
_JINJA_EXPRESSION = re.compile(r"\{\{.*?\}\}", re.S)


def strip_jinja(sql: str) -> str:
    """Jinja şablonlarını ayıklar: ``{{ params.db }}.TABLO`` → ``TABLO``; diğer ifadeler yer tutucu olur."""
    sql = _JINJA_COMMENT.sub(" ", sql)
    sql = _JINJA_STATEMENT.sub(" ", sql)
    sql = _JINJA_SCHEMA.sub("", sql)
    return _JINJA_EXPRESSION.sub(PLACEHOLDER, sql)


# --------------------------------------------------------------------------- #
# Okuyucu
# --------------------------------------------------------------------------- #


def read_airflow(builder: ModelBuilder, report: DocReport, path: Path) -> list[tuple[str, str]]:
    data = read_capped(path, report, MAX_BYTES)
    if data is None:
        return []
    try:
        tree = ast.parse(strip_bom(decode(data)), filename=path.name)
    except SyntaxError as exc:
        report.status = "error"
        report.note(f"Python sözdizimi hatası (satır {exc.lineno}): {exc.msg}")
        return []
    except (ValueError, RecursionError, MemoryError) as exc:
        report.status = "error"
        report.note(f"Python dosyası ayrıştırılamadı: {type(exc).__name__}")
        return []
    analyzer = _Analyzer(tree)
    try:
        try:
            analyzer.run()
        except _TooComplex:
            analyzer.note(f"dosya çok karmaşık; ilk {MAX_STEPS} deyim okundu")
        for message in analyzer.notes[:40]:
            report.note(message)
        if not analyzer.dags:
            report.status = "empty"
            report.note("dosyada Airflow DAG'ı bulunamadı")
            return []
        if not any(dag.tasks for dag in analyzer.dags):
            report.status = "empty"
            report.note("DAG'larda görev bulunamadı")
            return []
        _build(analyzer, builder, report, path.name)
    except RecursionError:
        report.status = "error"
        report.note("Python dosyası aşırı derin iç içe ifadeler içeriyor; okunamadı")
    return []


def _build(analyzer: _Analyzer, builder: ModelBuilder, report: DocReport, file: str) -> None:
    def source(line: int):
        return ref(file, f"satır {line}", EXTRACTOR, confidence=1.0)

    used: set[str] = set()
    keys: dict[_Task, str] = {}
    for dag in analyzer.dags:
        for task in dag.tasks.values():
            name = task.task_id if task.task_id.upper() not in used else f"{dag.dag_id}.{task.task_id}"
            used.add(name.upper())
            parts = [f"Airflow DAG: {dag.dag_id}", task.operator]
            if dag.schedule_note:
                parts.append(dag.schedule_note)
            keys[task] = builder.process(
                name, source(task.line), kind=task.kind, tool=TOOL, description=" · ".join(parts),
                schedule=dag.schedule.model_copy() if dag.schedule else None)
            report.processes += 1
    for dag in analyzer.dags:
        for task in dag.tasks.values():
            _task_lineage(analyzer, task, keys[task], builder, report, source(task.line))
            for dag_id, task_id in task.external:
                target = next((t for d in analyzer.dags if d.dag_id == dag_id for t in d.tasks.values()
                               if t.task_id == task_id), None)
                upstream = keys[target] if target else builder.process(task_id, source(task.line), tool=TOOL)
                builder.precedes(upstream, keys[task], source(task.line))
                report.flows += 1
        for first, second, line in dag.edges:
            builder.precedes(keys[first], keys[second], source(line))
            report.flows += 1


def _task_lineage(analyzer: _Analyzer, task: _Task, process: str, builder: ModelBuilder, report: DocReport,
                  source: Any) -> None:
    if task.call is None:
        return
    texts, files, dynamic = analyzer.task_sql(task)
    where = f"satır {task.line} · görev {task.task_id}"
    for name in files:
        report.note(f"{where}: SQL dosyası '{name}' okunmadı (dosyayı ayrıca yükleyebilirsiniz)")
    if dynamic:
        report.note(f"{where}: SQL sabit metin değil (değişken, f-string …); soyu çıkarılmadı")
    seen: set[tuple[str, str]] = set()
    for text in texts:
        result = analyze_sql(strip_jinja(text))
        if result.unparsed:
            report.note(f"{where}: {result.unparsed} SQL ifadesi ayrıştırılamadı")
        if result.unresolved:
            report.note(f"{where}: {result.unresolved} tablo adı şablona bağlı ({{{{ … }}}}), çözülemedi")
        for statement in result.statements:
            if statement.target:
                key = builder.dataset(statement.target, source)
                builder.writes(process, key, source)
                if (key, "w") not in seen:
                    seen.add((key, "w"))
                    report.flows += 1
            for name in statement.sources:
                key = builder.dataset(name, source)
                builder.reads(key, process, source)
                if (key, "r") not in seen:
                    seen.add((key, "r"))
                    report.flows += 1


# --------------------------------------------------------------------------- #
# İçerik tespiti ve kayıt
# --------------------------------------------------------------------------- #

_AIRFLOW_IMPORT = re.compile(r"^[ \t]*(?:from[ \t]+airflow(?:\.[\w.]+)?[ \t]+import\b|import[ \t]+airflow\b)", re.M)
_DAG_MARK = re.compile(r"\bDAG\b|@dag\b")


def looks_like_airflow(head: bytes, name: str) -> bool:
    """Airflow içe aktarması ve DAG işareti olan Python dosyası (``.py`` dışındaysa ayrıştırılabilmeli)."""
    text = sniff_text(head)
    if not (_AIRFLOW_IMPORT.search(text) and _DAG_MARK.search(text)):
        return False
    if name.lower().endswith(".py"):
        return True
    if len(head) >= SNIFF_BYTES:
        return False
    try:
        ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False
    return True


register(FormatSpec(
    "airflow", "Apache Airflow DAG", (".py",), read_airflow, _preview.preview_code, sniff=looks_like_airflow,
    group="Zamanlayıcı", sample="ornek_bicimler/kart_mart_dag.py",
    description="Airflow DAG'ı (Python; çalıştırılmadan ast ile okunur): görevler, >>/chain sırası, "
                "operatör SQL'i",
), primary=False)
