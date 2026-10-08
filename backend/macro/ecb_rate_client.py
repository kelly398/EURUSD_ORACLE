import csv
import hashlib
import io
import json
import ssl

from datetime import (
    date,
    datetime,
    timezone
)

from decimal import (
    Decimal,
    InvalidOperation
)

from urllib.error import (
    HTTPError,
    URLError
)

from urllib.parse import (
    urlencode
)

from urllib.request import (
    Request,
    urlopen
)

import certifi

from backend.macro.ecb_rate_protocol import (
    ECB_CENTRAL_BANK,
    ECB_RATE_ALLOWED_ACTIONS,
    ECB_RATE_CONTENT_HASH_FIELDS,
    ECB_RATE_REQUIRED_COLUMNS,
    ECB_RATE_SERIES,
    ECB_RATE_SOURCE,
    ECB_REQUEST_FORMAT,
    ECB_REQUEST_INCLUDE_HISTORY,
    ECB_SDMX_RATE_BASE_URL,
    build_ecb_rate_response_key,
    build_ecb_rate_source_record_id
)

from backend.macro.macro_schemas import (
    CentralBankRateSnapshot
)


class ECBRateClient:

    def __init__(
        self,
        timeout_seconds=30,
        opener=urlopen
    ):

        if (
            not isinstance(
                timeout_seconds,
                (
                    int,
                    float
                )
            )

            or

            timeout_seconds <= 0
        ):

            raise ValueError(
                "timeout_seconds must be "
                "greater than zero."
            )


        if not callable(
            opener
        ):

            raise TypeError(
                "opener must be callable."
            )


        self.timeout_seconds = (
            float(
                timeout_seconds
            )
        )

        self.opener = opener


        self.ssl_context = (
            ssl.create_default_context(
                cafile=certifi.where()
            )
        )


        self._series_by_key = {

            item.series_key:
                item

            for item
            in ECB_RATE_SERIES
        }


    def _validate_date(
        self,
        value,
        name
    ):

        if (
            type(
                value
            )
            is not date
        ):

            raise TypeError(
                f"{name} must be date."
            )


    def _validate_ingested_at(
        self,
        ingested_at
    ):

        if not isinstance(
            ingested_at,
            datetime
        ):

            raise TypeError(
                "ingested_at must be "
                "datetime."
            )


        if (
            ingested_at.tzinfo
            is None
        ):

            raise ValueError(
                "ingested_at must be "
                "timezone-aware."
            )


    def _format_date(
        self,
        value
    ):

        return value.isoformat()


    def build_url(
        self,
        series_key,
        start_date,
        end_date
    ):

        self._validate_date(
            start_date,
            "start_date"
        )

        self._validate_date(
            end_date,
            "end_date"
        )


        if (
            end_date
            < start_date
        ):

            raise ValueError(
                "end_date must be on or "
                "after start_date."
            )


        if (
            series_key
            not in self._series_by_key
        ):

            raise ValueError(
                "Unsupported ECB rate "
                f"series: {series_key}"
            )


        parameters = urlencode({

            "startPeriod":
                self._format_date(
                    start_date
                ),

            "endPeriod":
                self._format_date(
                    end_date
                ),

            "includeHistory":
                (
                    "true"
                    if ECB_REQUEST_INCLUDE_HISTORY
                    else "false"
                ),

            "format":
                ECB_REQUEST_FORMAT
        })


        return (
            ECB_SDMX_RATE_BASE_URL
            + series_key
            + "?"
            + parameters
        )


    def _decode_payload(
        self,
        payload
    ):

        if not isinstance(
            payload,
            (
                bytes,
                bytearray
            )
        ):

            raise TypeError(
                "ECB response payload must "
                "be bytes."
            )


        try:

            return bytes(
                payload
            ).decode(
                "utf-8-sig"
            )

        except UnicodeDecodeError as exc:

            raise ValueError(
                "ECB SDMX response is not "
                "valid UTF-8 CSV."
            ) from exc


    def _parse_csv(
        self,
        payload
    ):

        text = (
            self._decode_payload(
                payload
            )
        )


        reader = csv.DictReader(
            io.StringIO(
                text
            )
        )


        fieldnames = (
            reader.fieldnames
        )


        if not fieldnames:

            raise ValueError(
                "ECB SDMX CSV has no header."
            )


        missing_columns = [

            column

            for column
            in ECB_RATE_REQUIRED_COLUMNS

            if (
                column
                not in fieldnames
            )
        ]


        if missing_columns:

            raise ValueError(
                "ECB SDMX CSV missing required "
                "columns: "
                + ", ".join(
                    missing_columns
                )
            )


        rows = []


        for row in reader:

            if not isinstance(
                row,
                dict
            ):

                raise TypeError(
                    "ECB SDMX CSV row must "
                    "be mapping-like."
                )


            rows.append(
                row
            )


        return tuple(
            rows
        )


    def fetch_rows(
        self,
        series_key,
        start_date,
        end_date
    ):

        url = self.build_url(

            series_key=
                series_key,

            start_date=
                start_date,

            end_date=
                end_date
        )


        request = Request(

            url,

            headers={

                "Accept":
                    "text/csv",

                "User-Agent":
                    "EURUSD_ORACLE/1.0"
            }
        )


        try:

            with self.opener(

                request,

                timeout=
                    self.timeout_seconds,

                context=
                    self.ssl_context

            ) as response:

                payload = (
                    response.read()
                )


        except (
            HTTPError,
            URLError,
            TimeoutError,
            ssl.SSLError
        ) as exc:

            raise RuntimeError(
                "ECB SDMX request failed."
            ) from exc


        return (
            self._parse_csv(
                payload
            )
        )


    def _require(
        self,
        row,
        key
    ):

        if key not in row:

            raise ValueError(
                "ECB SDMX row missing "
                f"required field: {key}"
            )


        value = row[key]


        if value is None:

            return ""


        return str(
            value
        ).strip()


    def _parse_reference_date(
        self,
        raw
    ):

        try:

            return date.fromisoformat(
                raw
            )

        except ValueError as exc:

            raise ValueError(
                "ECB TIME_PERIOD is not "
                "a valid ISO date."
            ) from exc


    def _parse_available_at(
        self,
        raw
    ):

        try:

            value = (
                datetime.fromisoformat(
                    raw
                )
            )

        except ValueError as exc:

            raise ValueError(
                "ECB VALID_FROM is not "
                "a valid ISO datetime."
            ) from exc


        if (
            value.tzinfo
            is None
        ):

            raise ValueError(
                "ECB VALID_FROM must be "
                "timezone-aware."
            )


        return value.astimezone(
            timezone.utc
        )


    def _parse_value(
        self,
        raw
    ):

        try:

            value = Decimal(
                raw
            )

        except (
            InvalidOperation,
            ValueError
        ) as exc:

            raise ValueError(
                "ECB OBS_VALUE is not "
                "numeric."
            ) from exc


        if not value.is_finite():

            raise ValueError(
                "ECB OBS_VALUE must be "
                "finite."
            )


        return value


    def _canonical_decimal(
        self,
        value
    ):

        normalized = (
            value.normalize()
        )


        text = format(
            normalized,
            "f"
        )


        if (
            "." in text
        ):

            text = (
                text.rstrip(
                    "0"
                )
                .rstrip(
                    "."
                )
            )


        if text in (
            "",
            "-0"
        ):

            return "0"


        return text


    def _content_hash(
        self,
        payload
    ):

        if (
            tuple(
                payload.keys()
            )
            != ECB_RATE_CONTENT_HASH_FIELDS
        ):

            raise ValueError(
                "ECB rate hash payload does "
                "not match frozen protocol."
            )


        encoded = json.dumps(

            payload,

            sort_keys=True,

            separators=(
                ",",
                ":"
            ),

            ensure_ascii=False

        ).encode(
            "utf-8"
        )


        return hashlib.sha256(
            encoded
        ).hexdigest()


    def normalize_row(
        self,
        row,
        rate_name,
        expected_series_key,
        ingested_at
    ):

        if not isinstance(
            row,
            dict
        ):

            raise TypeError(
                "ECB rate row must be dict."
            )


        self._validate_ingested_at(
            ingested_at
        )


        if (
            expected_series_key
            not in self._series_by_key
        ):

            raise ValueError(
                "Unsupported expected ECB "
                "rate series."
            )


        spec = (
            self._series_by_key[
                expected_series_key
            ]
        )


        if (
            rate_name
            != spec.rate_name
        ):

            raise ValueError(
                "ECB rate_name does not "
                "match series contract."
            )


        row_key = self._require(
            row,
            "KEY"
        )


        expected_response_key = (
            build_ecb_rate_response_key(
                expected_series_key
            )
        )


        if (
            row_key
            != expected_response_key
        ):

            raise ValueError(
                "ECB SDMX row KEY does not "
                "match requested series."
            )


        raw_time_period = self._require(
            row,
            "TIME_PERIOD"
        )


        if not raw_time_period:

            raise ValueError(
                "ECB TIME_PERIOD must not "
                "be empty."
            )


        raw_value = self._require(
            row,
            "OBS_VALUE"
        )


        if not raw_value:

            raise ValueError(
                "ECB OBS_VALUE must not "
                "be empty."
            )


        action = self._require(
            row,
            "ACTION"
        )


        if (
            action
            not in ECB_RATE_ALLOWED_ACTIONS
        ):

            raise ValueError(
                "Unsupported ECB SDMX ACTION: "
                f"{action!r}"
            )


        raw_valid_from = self._require(
            row,
            "VALID_FROM"
        )


        if not raw_valid_from:

            raise ValueError(
                "ECB VALID_FROM must not "
                "be empty."
            )


        raw_valid_to = self._require(
            row,
            "VALID_TO"
        )


        if raw_valid_to:

            raise ValueError(
                "ECB rate v1 does not support "
                "non-empty VALID_TO."
            )


        reference_date = (
            self._parse_reference_date(
                raw_time_period
            )
        )


        available_at = (
            self._parse_available_at(
                raw_valid_from
            )
        )


        value_decimal = (
            self._parse_value(
                raw_value
            )
        )


        canonical_value = (
            self._canonical_decimal(
                value_decimal
            )
        )


        hash_payload = {

            "central_bank":
                ECB_CENTRAL_BANK,

            "rate_name":
                rate_name,

            "series_key":
                expected_series_key,

            "reference_date":
                reference_date.isoformat(),

            "value_percent":
                canonical_value,

            "source":
                ECB_RATE_SOURCE
        }


        content_hash = (
            self._content_hash(
                hash_payload
            )
        )


        source_record_id = (
            build_ecb_rate_source_record_id(

                series_key=
                    row_key,

                time_period=
                    raw_time_period
            )
        )


        return CentralBankRateSnapshot(

            central_bank=
                ECB_CENTRAL_BANK,

            rate_name=
                rate_name,

            reference_date=
                reference_date,

            available_at=
                available_at,

            value_percent=
                float(
                    value_decimal
                ),

            source_record_id=
                source_record_id,

            content_hash=
                content_hash,

            source=
                ECB_RATE_SOURCE,

            ingested_at=
                ingested_at.astimezone(
                    timezone.utc
                )
        )


    def normalize_rows(
        self,
        rows,
        rate_name,
        expected_series_key,
        ingested_at
    ):

        snapshots = tuple(

            self.normalize_row(

                row=row,

                rate_name=
                    rate_name,

                expected_series_key=
                    expected_series_key,

                ingested_at=
                    ingested_at

            )

            for row
            in rows
        )


        return tuple(

            sorted(

                snapshots,

                key=lambda item: (

                    item.reference_date,

                    item.rate_name,

                    item.source_record_id,

                    item.available_at,

                    item.content_hash
                )
            )
        )


    def fetch_series_snapshots(
        self,
        series_key,
        start_date,
        end_date,
        ingested_at
    ):

        self._validate_ingested_at(
            ingested_at
        )


        if (
            series_key
            not in self._series_by_key
        ):

            raise ValueError(
                "Unsupported ECB rate "
                f"series: {series_key}"
            )


        spec = (
            self._series_by_key[
                series_key
            ]
        )


        rows = self.fetch_rows(

            series_key=
                series_key,

            start_date=
                start_date,

            end_date=
                end_date
        )


        return self.normalize_rows(

            rows=rows,

            rate_name=
                spec.rate_name,

            expected_series_key=
                series_key,

            ingested_at=
                ingested_at
        )


    def fetch_snapshots(
        self,
        start_date,
        end_date,
        ingested_at
    ):

        self._validate_date(
            start_date,
            "start_date"
        )

        self._validate_date(
            end_date,
            "end_date"
        )

        self._validate_ingested_at(
            ingested_at
        )


        snapshots = []


        for spec in ECB_RATE_SERIES:

            snapshots.extend(

                self.fetch_series_snapshots(

                    series_key=
                        spec.series_key,

                    start_date=
                        start_date,

                    end_date=
                        end_date,

                    ingested_at=
                        ingested_at
                )
            )


        return tuple(

            sorted(

                snapshots,

                key=lambda item: (

                    item.reference_date,

                    item.rate_name,

                    item.source_record_id,

                    item.available_at,

                    item.content_hash
                )
            )
        )