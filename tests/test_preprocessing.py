"""本地时间窗口、小区映射及临床规则。 / Local windows, cell mappings and clinical rules."""

from datetime import date, datetime, timedelta, timezone

import pytest

from mini_mpc.applications.preprocessing import (
    HospitalCellMapping,
    TelcoLocationEvent,
    derive_hospital_clinical_need_score_from_view_row,
    derive_telco_features_from_events,
    derive_telco_features_from_hourly_view,
    derive_telco_hourly_presence_from_events,
    hospital_cell_mapping_from_view_row,
    telco_location_event_from_view_row,
)


def test_telco_preprocessing_derives_hourly_presence_from_events_and_mapping():
    events = [
        TelcoLocationEvent(
            patient_link_key="patient-link-001",
            location_timestamp=datetime(2026, 8, 28, 22, 15),
            nCGI="ncgi-hospital-001",
            TAI="tai-001",
        ),
        TelcoLocationEvent(
            patient_link_key="patient-link-001",
            location_timestamp=datetime(2026, 8, 29, 3, 5),
            nCGI="ncgi-hospital-001",
            TAI="tai-001",
        ),
        TelcoLocationEvent(
            patient_link_key="patient-link-002",
            location_timestamp=datetime(2026, 8, 29, 4, 5),
            nCGI="ncgi-hospital-001",
            TAI="tai-001",
        ),
        TelcoLocationEvent(
            patient_link_key="patient-link-001",
            location_timestamp=datetime(2026, 8, 29, 8, 0),
            nCGI="ncgi-hospital-001",
            TAI="tai-001",
        ),
    ]
    mappings = [
        HospitalCellMapping(
            hospital_id="hospital-001",
            nCGI="ncgi-hospital-001",
            TAI="tai-001",
            mapping_version="v1",
            valid_from=datetime(2026, 1, 1),
        )
    ]

    hourly_presence = derive_telco_hourly_presence_from_events(
        events,
        mappings,
        patient_link_key="patient-link-001",
        hospital_id="hospital-001",
        audit_date=date(2026, 8, 28),
    )

    assert hourly_presence == [1, 0, 0, 0, 0, 1, 0, 0, 0, 0]


def test_telco_preprocessing_rejects_wrong_tai_and_expired_mapping():
    events = [
        TelcoLocationEvent(
            patient_link_key="patient-link-001",
            location_timestamp=datetime(2026, 8, 29, 2, 15),
            nCGI="ncgi-hospital-001",
            TAI="wrong-tai",
        ),
        TelcoLocationEvent(
            patient_link_key="patient-link-001",
            location_timestamp=datetime(2026, 8, 29, 5, 15),
            nCGI="ncgi-expired-001",
            TAI="tai-001",
        ),
    ]
    mappings = [
        HospitalCellMapping(
            hospital_id="hospital-001",
            nCGI="ncgi-hospital-001",
            TAI="tai-001",
            valid_from=datetime(2026, 1, 1),
        ),
        HospitalCellMapping(
            hospital_id="hospital-001",
            nCGI="ncgi-expired-001",
            TAI="tai-001",
            valid_from=datetime(2025, 1, 1),
            valid_to=datetime(2026, 1, 1),
        ),
    ]

    hourly_presence = derive_telco_hourly_presence_from_events(
        events,
        mappings,
        patient_link_key="patient-link-001",
        hospital_id="hospital-001",
        audit_date=date(2026, 8, 28),
    )

    assert hourly_presence == [0] * 10


def test_telco_preprocessing_reuses_existing_feature_derivation():
    features = derive_telco_features_from_events(
        [
            TelcoLocationEvent(
                patient_link_key="patient-link-001",
                location_timestamp=datetime(2026, 8, 28, 22, 10),
                nCGI="ncgi-hospital-001",
                TAI="tai-001",
            ),
            TelcoLocationEvent(
                patient_link_key="patient-link-001",
                location_timestamp=datetime(2026, 8, 28, 23, 10),
                nCGI="ncgi-hospital-001",
                TAI="tai-001",
            ),
        ],
        [
            HospitalCellMapping(
                hospital_id="hospital-001",
                nCGI="ncgi-hospital-001",
                TAI="tai-001",
                valid_from=datetime(2026, 1, 1),
            )
        ],
        patient_link_key="patient-link-001",
        hospital_id="hospital-001",
        audit_date=date(2026, 8, 28),
    )

    assert features.telco_absence_hours == 8
    assert features.telco_max_absence_streak == 8


def test_telco_hourly_view_preprocessing_keeps_existing_view_path():
    row = {
        "presence_h00": 1,
        "presence_h01": 1,
        "presence_h02": 0,
        "presence_h03": 0,
        "presence_h04": 0,
        "presence_h05": 1,
        "presence_h06": 1,
        "presence_h07": 0,
        "presence_h08": 0,
        "presence_h09": 1,
    }

    features = derive_telco_features_from_hourly_view(row)

    assert features.telco_absence_hours == 5
    assert features.telco_max_absence_streak == 3


def test_hospital_preprocessing_uses_direct_score_or_local_rule_table():
    assert (
        derive_hospital_clinical_need_score_from_view_row({"clinical_need_score": 6})
        == 6
    )

    score = derive_hospital_clinical_need_score_from_view_row(
        {
            "diagnosis_code": "J18.9",
            "disease_category": "respiratory",
            "severity_level": "moderate",
        }
    )

    assert score == 6


def test_hospital_preprocessing_rejects_unknown_clinical_rule():
    with pytest.raises(ValueError):
        derive_hospital_clinical_need_score_from_view_row(
            {
                "diagnosis_code": "X00",
                "disease_category": "unknown",
                "severity_level": "moderate",
            }
        )


def test_raw_telco_rows_convert_to_internal_preprocessing_objects():
    event = telco_location_event_from_view_row(
        {
            "subscriber_id": "subscriber-001",
            "patient_link_key": "patient-link-001",
            "location_timestamp": datetime(2026, 8, 28, 22, 15),
            "nCGI": "ncgi-hospital-001",
            "TAI": "tai-001",
        }
    )
    mapping = hospital_cell_mapping_from_view_row(
        {
            "hospital_id": "hospital-001",
            "nCGI": "ncgi-hospital-001",
            "TAI": "tai-001",
            "mapping_version": "v1",
            "valid_from": datetime(2026, 1, 1),
            "valid_to": None,
        }
    )

    assert event.patient_link_key == "patient-link-001"
    assert event.location_timestamp == datetime(2026, 8, 28, 22, 15)
    assert mapping.hospital_id == "hospital-001"
    assert mapping.valid_to is None


def test_aware_events_use_explicit_audit_timezone():
    # UTC 14:15 对应审计时区 UTC+8 的 22:15，应进入第一个小时。
    # 14:15 UTC is 22:15 in UTC+8 and belongs to the first audit hour.
    event = TelcoLocationEvent(
        "patient", datetime(2026, 8, 28, 14, 15, tzinfo=timezone.utc), "cell", "area",
    )
    mapping = HospitalCellMapping(
        "hospital", "cell", "area", datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    args = dict(patient_link_key="patient", hospital_id="hospital", audit_date=date(2026, 8, 28),
                audit_timezone=timezone(timedelta(hours=8)))
    assert derive_telco_hourly_presence_from_events([event], [mapping], **args) == [1] + [0] * 9
    features = derive_telco_features_from_events([event], [mapping], **args)
    assert (features.telco_absence_hours, features.telco_max_absence_streak) == (9, 9)


def test_aware_events_require_an_audit_timezone():
    event = TelcoLocationEvent("patient", datetime(2026, 8, 28, 22, tzinfo=timezone.utc), "cell")
    with pytest.raises(ValueError, match="timezone awareness"):
        derive_telco_hourly_presence_from_events(
            [event], [], patient_link_key="patient", hospital_id="hospital", audit_date=date(2026, 8, 28),
        )


def test_event_pipeline_rejects_mixed_timestamp_modes():
    event = TelcoLocationEvent("patient", datetime(2026, 8, 28, 22, tzinfo=timezone.utc), "cell")
    mapping = HospitalCellMapping("hospital", "cell", None, datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="timezone awareness"):
        derive_telco_hourly_presence_from_events(
            [event], [mapping], patient_link_key="patient", hospital_id="hospital",
            audit_date=date(2026, 8, 28), audit_timezone=timezone.utc,
        )


def test_mapping_rejects_mixed_validity_timestamp_modes():
    with pytest.raises(ValueError, match="timezone awareness"):
        HospitalCellMapping(
            "hospital", "cell", None, datetime(2026, 1, 1),
            datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
