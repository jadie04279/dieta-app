"""Unit tests for core/health_flags.py."""
import pytest
from core.health_flags import check_health_flags


class TestCheckHealthFlags:
    def test_normal_values_no_flags(self):
        result = check_health_flags({
            "fasting_glucose": 90, "sbp": 120, "dbp": 75,
            "ldl": 120, "triglyceride": 150,
        })
        assert result == []

    def test_empty_input_no_flags(self):
        assert check_health_flags({}) == []
        assert check_health_flags(None) == []

    def test_diabetes_flag(self):
        flags = check_health_flags({"fasting_glucose": 130})
        codes = [f["code"] for f in flags]
        assert "diabetes_risk" in codes

    def test_prediabetes_flag(self):
        flags = check_health_flags({"fasting_glucose": 110})
        codes = [f["code"] for f in flags]
        assert "prediabetes_risk" in codes
        assert "diabetes_risk" not in codes

    def test_prediabetes_boundary_99(self):
        flags = check_health_flags({"fasting_glucose": 99})
        assert flags == []

    def test_diabetes_boundary_126(self):
        flags = check_health_flags({"fasting_glucose": 126})
        codes = [f["code"] for f in flags]
        assert "diabetes_risk" in codes

    def test_hypertension_sbp(self):
        flags = check_health_flags({"sbp": 145, "dbp": 85})
        codes = [f["code"] for f in flags]
        assert "hypertension" in codes

    def test_hypertension_dbp(self):
        flags = check_health_flags({"sbp": 130, "dbp": 92})
        codes = [f["code"] for f in flags]
        assert "hypertension" in codes

    def test_normal_bp_no_flag(self):
        flags = check_health_flags({"sbp": 125, "dbp": 82})
        assert all(f["code"] != "hypertension" for f in flags)

    def test_high_ldl_flag(self):
        flags = check_health_flags({"ldl": 165})
        codes = [f["code"] for f in flags]
        assert "high_ldl" in codes

    def test_normal_ldl_no_flag(self):
        flags = check_health_flags({"ldl": 150})
        assert all(f["code"] != "high_ldl" for f in flags)

    def test_high_triglyceride_flag(self):
        flags = check_health_flags({"triglyceride": 210})
        codes = [f["code"] for f in flags]
        assert "high_triglyceride" in codes

    def test_normal_triglyceride_no_flag(self):
        flags = check_health_flags({"triglyceride": 190})
        assert all(f["code"] != "high_triglyceride" for f in flags)

    def test_multiple_flags_all_returned(self):
        flags = check_health_flags({
            "fasting_glucose": 140,
            "sbp": 150, "dbp": 95,
            "ldl": 170,
            "triglyceride": 250,
        })
        codes = {f["code"] for f in flags}
        assert "diabetes_risk" in codes
        assert "hypertension" in codes
        assert "high_ldl" in codes
        assert "high_triglyceride" in codes

    def test_diabetes_modifier_is_conservative_exercise(self):
        flags = check_health_flags({"fasting_glucose": 130})
        flag = next(f for f in flags if f["code"] == "diabetes_risk")
        assert flag["modifier"] == "conservative_exercise"

    def test_severity_levels_correct(self):
        flags = check_health_flags({
            "fasting_glucose": 130,  # error
            "ldl": 170,              # warning
        })
        by_code = {f["code"]: f for f in flags}
        assert by_code["diabetes_risk"]["severity"] == "error"
        assert by_code["high_ldl"]["severity"] == "warning"

    def test_zero_values_treated_as_missing(self):
        # 0 should not trigger flags (entered as "not measured")
        flags = check_health_flags({
            "fasting_glucose": 0, "sbp": 0, "dbp": 0,
            "ldl": 0, "triglyceride": 0,
        })
        assert flags == []
