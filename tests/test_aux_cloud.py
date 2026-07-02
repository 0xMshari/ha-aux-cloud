"""Tests for the AuxCloudAPI class."""

from datetime import date
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from custom_components.aux_cloud.api.aux_cloud import (
    AuxCloudAPI,
    API_SERVER_URL_EU,
    API_SERVER_URL_USA,
    API_SERVER_URL_CN,
    _build_stats_date_range,
    _product_id_to_devtype,
    build_stats_date_range_from_dates,
    resolve_stats_report_type,
    parse_device_stats_total,
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
        """Test the webapi headers' generation."""
        aux_api.loginsession = "test_session"
        aux_api.userid = "test_user"
        headers = aux_api._get_webapi_headers(familyid="family123")

        assert headers["Content-Type"] == "application/json"
        assert headers["familyid"] == "family123"
        assert headers["loginsession"] == "test_session"
        assert headers["userid"] == "test_user"

    def test_product_id_to_devtype(self):
        """Test productId to devtype conversion."""
        assert (
            _product_id_to_devtype("000000000000000000000000c3aa0000") == 43715
        )

    def test_build_stats_date_range(self):
        """Test stats date range generation."""
        ref = date(2026, 6, 26)

        assert _build_stats_date_range("year", ref) == (
            "2026-01-00_00:00:00",
            "2026-12-31_23:59:59",
        )
        assert _build_stats_date_range("month", ref) == (
            "2026-06-00_00:00:00",
            "2026-06-30_23:59:59",
        )
        assert _build_stats_date_range("day", ref) == (
            "2026-06-26_00:00:00",
            "2026-06-26_23:59:59",
        )

    def test_build_stats_date_range_from_dates(self):
        """Test arbitrary date range formatting."""
        assert build_stats_date_range_from_dates(
            date(2026, 6, 20), date(2026, 6, 26)
        ) == (
            "2026-06-20_00:00:00",
            "2026-06-26_23:59:59",
        )

    def test_resolve_stats_report_type(self):
        """Test automatic report granularity selection."""
        assert resolve_stats_report_type(date(2026, 6, 1), date(2026, 6, 7)) == "day"
        assert resolve_stats_report_type(date(2026, 1, 1), date(2026, 6, 1)) == "month"
        assert resolve_stats_report_type(date(2024, 1, 1), date(2026, 1, 1)) == "year"

    @pytest.mark.asyncio
    async def test_get_device_stats(self, aux_api):
        """Test device stats query payload and endpoint."""
        aux_api.loginsession = "session"
        aux_api.userid = "user"
        device = {
            "endpointId": "00000000000000000000907abe0f24f8",
            "productId": "000000000000000000000000c3aa0000",
            "familyid": "0e7b1905269014b474cb8eaa78df3a4d",
        }
        expected_response = {"device": [{"data": [{"val": 12.5}]}]}

        with patch.object(
            aux_api, "_make_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = expected_response
            result = await aux_api.get_device_stats(
                device,
                report_type="year",
                ref_date=date(2026, 1, 1),
            )

        assert result == expected_response
        mock_request.assert_awaited_once()
        call_kwargs = mock_request.call_args.kwargs
        assert call_kwargs["method"] == "POST"
        assert call_kwargs["endpoint"] == "appfront/v1/webapi/device/stats"
        assert call_kwargs["headers"]["familyid"] == device["familyid"]
        assert call_kwargs["data"]["report"] == "fw_auxoverseayearconsum_v1"
        assert call_kwargs["data"]["devtype"] == 43715
        assert call_kwargs["data"]["device"][0]["reportType"] == "year"
        assert call_kwargs["data"]["device"][0]["params"] == ["tenelec"]
        assert call_kwargs["data"]["device"][0]["start"] == "2026-01-00_00:00:00"
        assert call_kwargs["data"]["device"][0]["end"] == "2026-12-31_23:59:59"

    @pytest.mark.asyncio
    async def test_get_device_stats_report_types(self, aux_api):
        """Test report name mapping for each report type."""
        aux_api.loginsession = "session"
        aux_api.userid = "user"
        device = {
            "endpointId": "did",
            "productId": "000000000000000000000000c3aa0000",
            "familyid": "family",
        }

        report_map = {
            "day": "fw_auxoverseadayconsum_v1",
            "month": "fw_auxoverseamonthconsum_v1",
            "year": "fw_auxoverseayearconsum_v1",
        }

        with patch.object(
            aux_api, "_make_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {}
            for report_type, report_name in report_map.items():
                await aux_api.get_device_stats(
                    device,
                    report_type=report_type,
                    ref_date=date(2026, 6, 26),
                )
                assert (
                    mock_request.call_args.kwargs["data"]["report"] == report_name
                )
                assert (
                    mock_request.call_args.kwargs["data"]["device"][0]["reportType"]
                    == report_type
                )

    @pytest.mark.asyncio
    async def test_get_device_stats_custom_period(self, aux_api):
        """Test stats query for an arbitrary date range."""
        aux_api.loginsession = "session"
        aux_api.userid = "user"
        device = {
            "endpointId": "did",
            "productId": "000000000000000000000000c3aa0000",
            "familyid": "family",
        }

        with patch.object(
            aux_api, "_make_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {}
            await aux_api.get_device_stats_for_period(
                device,
                date(2026, 6, 20),
                date(2026, 6, 26),
            )

        call_kwargs = mock_request.call_args.kwargs
        assert call_kwargs["data"]["report"] == "fw_auxoverseadayconsum_v1"
        assert call_kwargs["data"]["device"][0]["start"] == "2026-06-20_00:00:00"
        assert call_kwargs["data"]["device"][0]["end"] == "2026-06-26_23:59:59"
        assert call_kwargs["data"]["device"][0]["reportType"] == "day"

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

    def test_parse_device_stats_total_from_total_field(self):
        """Test reading values from legacy device.data rows."""
        response = {"device": [{"data": [{"tenelec": 42.5}]}]}
        assert parse_device_stats_total(response) == 42.5

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
