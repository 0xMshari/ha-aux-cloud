"""Tests for the AuxCloudAPI class."""

from datetime import date
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from custom_components.aux_cloud.api.aux_cloud import (
    AuxApiError,
    AuxCloudAPI,
    API_SERVER_URL_EU,
    API_SERVER_URL_USA,
    API_SERVER_URL_CN,
    _build_stats_date_range,
    _product_id_to_devtype,
    aggregate_device_stats_by_day,
    build_stats_date_range_from_dates,
    parse_device_stats_latest,
    parse_device_stats_total,
    resolve_stats_report_type,
)


@pytest.fixture
def aux_api():
    """Return a new AuxCloudAPI instance."""
    return AuxCloudAPI(region="eu")


@pytest.fixture
def mock_response():
    """Return a mock response for API calls."""
    mock = MagicMock()
    mock.status = 200
    mock.text = AsyncMock(return_value='{"status": 0, "data": {}}')
    return mock


class TestAuxCloudAPI:
    """Tests for the AuxCloudAPI class."""

    def test_init(self):
        """Test initialization with different regions."""
        api_eu = AuxCloudAPI(region="eu")
        assert api_eu.url == API_SERVER_URL_EU
        assert api_eu.region == "eu"

        api_usa = AuxCloudAPI(region="usa")
        assert api_usa.url == API_SERVER_URL_USA
        assert api_usa.region == "usa"

        api_cn = AuxCloudAPI(region="cn")
        assert api_cn.url == API_SERVER_URL_CN
        assert api_cn.region == "cn"

        # Test default fallback
        api_unknown = AuxCloudAPI(region="unknown")
        assert api_unknown.url == API_SERVER_URL_EU
        assert api_unknown.region == "unknown"

    def test_get_headers(self, aux_api):
        """Test the headers' generation."""
        # Basic headers
        headers = aux_api._get_headers()
        assert "Content-Type" in headers
        assert headers["loginsession"] == ""
        assert headers["userid"] == ""

        # With login session and user ID
        aux_api.loginsession = "test_session"
        aux_api.userid = "test_user"
        headers = aux_api._get_headers()
        assert headers["loginsession"] == "test_session"
        assert headers["userid"] == "test_user"

        # With additional kwargs
        headers = aux_api._get_headers(custom_header="custom_value")
        assert headers["custom_header"] == "custom_value"

    def test_get_webapi_headers(self, aux_api):
        """Webapi headers must include familyid and JSON content type."""
        aux_api.loginsession = "sess"
        aux_api.userid = "user"
        headers = aux_api._get_webapi_headers(familyid="fam1")
        assert headers["familyid"] == "fam1"
        assert headers["Content-Type"] == "application/json"
        assert headers["loginsession"] == "sess"

    def test_product_id_to_devtype(self):
        """devtype is little-endian from productId bytes."""
        assert _product_id_to_devtype("000000000000000000000000c0620000") == 25056

    def test_build_stats_date_range(self):
        """Day/month/year ranges follow AUX quirks."""
        ref = date(2026, 7, 18)
        assert _build_stats_date_range("day", ref) == (
            "2026-07-18_00:00:00",
            "2026-07-18_23:59:59",
        )
        assert _build_stats_date_range("month", ref)[0] == "2026-07-00_00:00:00"
        assert _build_stats_date_range("year", ref)[0] == "2026-01-00_00:00:00"

    def test_build_stats_date_range_from_dates(self):
        """Arbitrary inclusive ranges preserve real day dates for day reports."""
        assert build_stats_date_range_from_dates(
            date(2026, 7, 1), date(2026, 7, 18), "day"
        ) == ("2026-07-01_00:00:00", "2026-07-18_23:59:59")

    def test_resolve_stats_report_type(self):
        """Short spans use day reports; longer spans use month/year."""
        assert (
            resolve_stats_report_type(date(2026, 7, 1), date(2026, 7, 18)) == "day"
        )
        assert (
            resolve_stats_report_type(date(2026, 1, 1), date(2026, 7, 18)) == "month"
        )
        assert (
            resolve_stats_report_type(date(2024, 1, 1), date(2026, 7, 18)) == "year"
        )

    @pytest.mark.asyncio
    async def test_get_device_stats(self, aux_api):
        """Stats request should hit the webapi endpoint with expected payload."""
        aux_api.loginsession = "sess"
        aux_api.userid = "user"
        device = {
            "endpointId": "did1",
            "productId": "000000000000000000000000c0620000",
            "familyid": "family",
        }

        with patch.object(
            aux_api, "_make_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"status": 0, "table": []}
            await aux_api.get_device_stats_for_period(
                device, date(2026, 7, 1), date(2026, 7, 18)
            )

        call_kwargs = mock_request.call_args.kwargs
        assert call_kwargs["endpoint"] == "appfront/v1/webapi/device/stats"
        assert call_kwargs["data"]["report"] == "fw_auxoverseadayconsum_v1"
        assert call_kwargs["data"]["device"][0]["params"] == ["tenelec"]
        assert call_kwargs["data"]["device"][0]["start"] == "2026-07-01_00:00:00"
        assert call_kwargs["data"]["device"][0]["end"] == "2026-07-18_23:59:59"
        assert call_kwargs["data"]["device"][0]["reportType"] == "day"

    @pytest.mark.asyncio
    async def test_get_device_stats_custom_period_month(self, aux_api):
        """Longer spans auto-select month report and MM-00 start."""
        aux_api.loginsession = "sess"
        aux_api.userid = "user"
        device = {
            "endpointId": "did1",
            "productId": "000000000000000000000000c3aa0000",
            "familyid": "family",
        }

        with patch.object(
            aux_api, "_make_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {}
            await aux_api.get_device_stats_for_period(
                device,
                date(2026, 1, 1),
                date(2026, 6, 1),
            )

        call_kwargs = mock_request.call_args.kwargs
        assert call_kwargs["data"]["report"] == "fw_auxoverseamonthconsum_v1"
        assert call_kwargs["data"]["device"][0]["start"] == "2026-01-00_00:00:00"
        assert call_kwargs["data"]["device"][0]["end"] == "2026-06-30_23:59:59"
        assert call_kwargs["data"]["device"][0]["reportType"] == "month"

    @pytest.mark.asyncio
    async def test_get_special_device_params_one_request_per_param(self, aux_api):
        """Special params must be fetched one at a time and merged."""
        device = {"endpointId": "did"}

        async def fake_get_device_params(dev, params=None):
            if params == ["mode"]:
                return {"mode": 2}
            if params == ["tenelec"]:
                return {"tenelec": 1250}
            raise AssertionError(f"Unexpected params: {params}")

        with patch.object(
            aux_api, "get_device_params", side_effect=fake_get_device_params
        ) as mock_get:
            merged = await aux_api.get_special_device_params(
                device, ["mode", "tenelec"]
            )

        assert merged == {"mode": 2, "tenelec": 1250}
        assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_get_special_device_params_tolerates_failures(self, aux_api):
        """One unsupported special param must not break the others."""
        device = {"endpointId": "did"}

        async def fake_get_device_params(dev, params=None):
            if params == ["tenelec"]:
                raise AuxApiError("param not supported")
            return {"mode": 1}

        with patch.object(
            aux_api, "get_device_params", side_effect=fake_get_device_params
        ):
            merged = await aux_api.get_special_device_params(
                device, ["mode", "tenelec"]
            )

        assert merged == {"mode": 1}

    def test_parse_device_stats_total_from_rows(self):
        """Test summing energy values from stats rows."""
        response = {
            "device": [
                {
                    "data": [
                        {"occurtime": "2026-06-26_10:00:00", "tenelec": 1.2},
                        {"occurtime": "2026-06-26_11:00:00", "tenelec": "0.8"},
                    ]
                }
            ]
        }
        assert parse_device_stats_total(response) == 2.0

    def test_parse_device_stats_total_from_table(self):
        """Test reading values from table response format."""
        response = {
            "msg": "ok",
            "status": 0,
            "table": [
                {
                    "cnt": 2,
                    "did": "00000000000000000000907abe09717d",
                    "total": 2,
                    "values": [
                        {"occurtime": "2026-06-26_10:00:00", "tenelec": 1.1875},
                        {"occurtime": "2026-06-26_11:00:00", "tenelec": 1.15625},
                    ],
                }
            ],
        }
        assert parse_device_stats_total(response) == 2.34375

    def test_aggregate_device_stats_by_day(self):
        """Hourly buckets should sum into calendar-day totals."""
        values = [
            {"occurtime": "2026-07-01_08:00:00", "tenelec": 0.5},
            {"occurtime": "2026-07-01_09:00:00", "tenelec": 1.5},
            {"occurtime": "2026-07-02_01:00:00", "tenelec": 2.0},
            {"occurtime": "2026-07-02_02:00:00", "tenelec": -1.0},  # skipped
        ]
        assert aggregate_device_stats_by_day(values) == {
            "2026-07-01": 2.0,
            "2026-07-02": 2.0,
        }

    def test_parse_device_stats_latest(self):
        """Latest helper should pick the newest non-negative occurtime bucket."""
        values = [
            {"occurtime": "2026-07-01_08:00:00", "tenelec": 0.5},
            {"occurtime": "2026-07-18_23:00:00", "tenelec": 1.8125},
            {"occurtime": "2026-07-10_12:00:00", "tenelec": 0.25},
            {"occurtime": "2026-07-19_00:00:00", "tenelec": -0.5},
        ]
        assert parse_device_stats_latest(values) == (
            "2026-07-18_23:00:00",
            1.8125,
        )
