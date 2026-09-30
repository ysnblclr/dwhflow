"""Kart mart gece yüklemesi (Airflow DAG'ı).

ODS kart işlemlerinden DWH olgu/boyut tablolarını, mart özetini ve BDDK bildirim
tablosunu üretir. Teradata bağlantısı ``teradata_dwh`` Airflow bağlantısından okunur.
ODS yüklemesi ``kart_ods_gece`` DAG'ındaki ``sp_ods_kart`` görevinin bitmesini bekler.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.decorators import task
from airflow.models.baseoperator import chain
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator
from airflow.providers.common.sql.operators.sql import SQLCheckOperator, SQLExecuteQueryOperator
from airflow.sensors.external_task import ExternalTaskSensor
from airflow.utils.task_group import TaskGroup

log = logging.getLogger(__name__)

VARSAYILANLAR = {
    "owner": "dwh-etl",
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
    "email": ["dwh-ops@banka.com.tr"],
    "email_on_failure": True,
    "conn_id": "teradata_dwh",
}

with DAG(
    dag_id="kart_mart_gece",
    description="Kart işlemleri: boyut, olgu, mart ve BDDK bildirimi",
    schedule="30 2 * * *",
    start_date=pendulum.datetime(2024, 1, 1, tz="Europe/Istanbul"),
    catchup=False,
    max_active_runs=1,
    default_args=VARSAYILANLAR,
    template_searchpath=["/opt/airflow/dags/sql"],
    tags=["dwh", "kart", "bddk"],
) as dag:
    basla = EmptyOperator(task_id="basla")

    ods_kart_bekle = ExternalTaskSensor(
        task_id="ods_kart_bekle",
        external_dag_id="kart_ods_gece",
        external_task_id="sp_ods_kart",
        mode="reschedule",
        poke_interval=300,
        timeout=3 * 60 * 60,
    )

    load_d_musteri = SQLExecuteQueryOperator(
        task_id="load_d_musteri",
        sql="""
            MERGE INTO DWH.D_MUSTERI d
            USING ODS_MUSTERI o
               ON d.MUSTERI_ID = o.MUSTERI_ID
            WHEN MATCHED THEN UPDATE SET SEGMENT = o.SEGMENT, SUBE_KODU = o.SUBE_KODU
            WHEN NOT MATCHED THEN INSERT (MUSTERI_ID, SEGMENT, SUBE_KODU)
                 VALUES (o.MUSTERI_ID, o.SEGMENT, o.SUBE_KODU);
        """,
    )

    load_f_kart_islem = SQLExecuteQueryOperator(
        task_id="load_f_kart_islem",
        sql="""
            INSERT INTO DWH.F_KART_ISLEM (ISLEM_ID, MUSTERI_KEY, ISLEM_TARIHI, TUTAR_TL)
            SELECT k.ISLEM_ID, m.MUSTERI_KEY, k.ISLEM_TARIHI, k.TUTAR
              FROM ODS_KART_ISLEM k
              JOIN DWH.D_MUSTERI m ON m.MUSTERI_ID = k.MUSTERI_ID
             WHERE k.ISLEM_TARIHI = DATE '{{ ds }}'
               AND k.DURUM = 'A'
        """,
    )

    load_mart_kart_ozet = SQLExecuteQueryOperator(
        task_id="load_mart_kart_ozet",
        sql=[
            "DELETE FROM DWH.MART_KART_OZET WHERE ISLEM_TARIHI = DATE '{{ ds }}'",
            """
            INSERT INTO DWH.MART_KART_OZET (SEGMENT, ISLEM_TARIHI, TOPLAM_TUTAR)
            SELECT m.SEGMENT, f.ISLEM_TARIHI, SUM(f.TUTAR_TL)
              FROM DWH.F_KART_ISLEM f
              JOIN DWH.D_MUSTERI m ON m.MUSTERI_KEY = f.MUSTERI_KEY
             WHERE f.ISLEM_TARIHI = DATE '{{ ds }}'
             GROUP BY m.SEGMENT, f.ISLEM_TARIHI
            """,
        ],
    )

    with TaskGroup("kontroller", tooltip="Veri kalitesi kontrolleri") as kontroller:
        dq_f_kart_islem = SQLCheckOperator(
            task_id="dq_f_kart_islem",
            sql="SELECT COUNT(*) > 0 FROM DWH.F_KART_ISLEM WHERE ISLEM_TARIHI = DATE '{{ ds }}'",
        )
        dq_mart_kart_ozet = SQLCheckOperator(
            task_id="dq_mart_kart_ozet",
            sql="""
                SELECT COUNT(*) = 0
                  FROM DWH.MART_KART_OZET
                 WHERE ISLEM_TARIHI = DATE '{{ ds }}'
                   AND TOPLAM_TUTAR < 0
            """,
        )

    exp_bddk = SQLExecuteQueryOperator(
        task_id="exp_bddk",
        sql="""
            INSERT INTO EXP_BDDK_KART (DONEM, SEGMENT, ISLEM_TARIHI, TOPLAM_TUTAR)
            SELECT '{{ ds_nodash[:6] }}', SEGMENT, ISLEM_TARIHI, TOPLAM_TUTAR
              FROM DWH.MART_KART_OZET
             WHERE ISLEM_TARIHI = DATE '{{ ds }}'
        """,
    )

    bddk_dosya_gonder = BashOperator(
        task_id="bddk_dosya_gonder",
        bash_command="/etl/bin/bddk_gonder.sh {{ ds_nodash }}",
        retries=0,
    )

    istatistik_topla = SQLExecuteQueryOperator(
        task_id="istatistik_topla",
        sql="kart_istatistik.sql",
    )

    @task
    def mart_ozeti_bildir(ds: str | None = None) -> None:
        """Mart özetinin hazır olduğunu operasyon kanalına bildirir."""
        log.info("%s tarihli kart mart özeti hazır", ds)

    bitis = EmptyOperator(task_id="bitis", trigger_rule="none_failed")

    basla >> [ods_kart_bekle, load_d_musteri]
    [ods_kart_bekle, load_d_musteri] >> load_f_kart_islem
    chain(load_f_kart_islem, load_mart_kart_ozet, kontroller, [exp_bddk, istatistik_topla])
    exp_bddk >> bddk_dosya_gonder >> bitis
    istatistik_topla >> mart_ozeti_bildir() >> bitis
