import hashlib
import json

from datetime import (
    date,
    datetime,
    timezone
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

from backend.institutional.cftc_release_calendar import (
    CFTCReleaseCalendar
)

from backend.institutional.cot_schemas import (
    COTPositioningSnapshot,
    COT_REPORT_TYPE_TFF_FUTURES_ONLY,
    EURO_FX_CFTC_CONTRACT_MARKET_CODE
)


CFTC_TFF_FUTURES_ONLY_DATASET_ID = (
    "gpe5-46if"
)

CFTC_TFF_FUTURES_ONLY_API = (
    "https://publicreporting.cftc.gov/"
    "resource/"
    f"{CFTC_TFF_FUTURES_ONLY_DATASET_ID}"
    ".json"
)


class CFTCTFFFuturesOnlyClient:

    def __init__(
        self,
        timeout_seconds=30
    ):

        if (
            timeout_seconds
            <= 0
        ):

            raise ValueError(
                "timeout_seconds must "
                "be positive."
            )


        self.timeout_seconds = (
            timeout_seconds
        )

        self.release_calendar = (
            CFTCReleaseCalendar()
        )


    def _validate_date(
        self,
        value,
        name
    ):

        if (
            type(value)
            is not date
        ):

            raise TypeError(
                f"{name} must be date."
            )


    def _format_api_date(
        self,
        value
    ):

        return (
            value.strftime(
                "%Y-%m-%d"
            )
            + "T00:00:00.000"
        )


    def build_url(
        self,
        start_report_date,
        end_report_date,
        limit=50000
    ):

        self._validate_date(
            start_report_date,
            "start_report_date"
        )

        self._validate_date(
            end_report_date,
            "end_report_date"
        )


        if (
            end_report_date
            < start_report_date
        ):

            raise ValueError(
                "end_report_date cannot "
                "precede start_report_date."
            )


        if limit <= 0:

            raise ValueError(
                "limit must be positive."
            )


        start_value = (
            self._format_api_date(
                start_report_date
            )
        )

        end_value = (
            self._format_api_date(
                end_report_date
            )
        )


        where = (

            "cftc_contract_market_code="
            f"'{EURO_FX_CFTC_CONTRACT_MARKET_CODE}'"

            " AND "

            "futonly_or_combined='FutOnly'"

            " AND "

            "report_date_as_yyyy_mm_dd"
            f">='{start_value}'"

            " AND "

            "report_date_as_yyyy_mm_dd"
            f"<='{end_value}'"
        )


        query = urlencode({

            # Socrata system fields are required
            # for causal revision provenance.
            "$select":
                ":*,*",

            "$where":
                where,

            "$order":
                (
                    "report_date_as_yyyy_mm_dd "
                    "ASC"
                ),

            "$limit":
                str(
                    limit
                )
        })


        return (
            CFTC_TFF_FUTURES_ONLY_API
            + "?"
            + query
        )


    def fetch_rows(
        self,
        start_report_date,
        end_report_date,
        limit=50000
    ):

        url = self.build_url(
            start_report_date=
                start_report_date,

            end_report_date=
                end_report_date,

            limit=
                limit
        )


        request = Request(

            url,

            headers={

                "Accept":
                    "application/json",

                "User-Agent":
                    "EURUSD-ORACLE/1.0"
            }
        )


        try:

            with urlopen(
                request,
                timeout=self.timeout_seconds
            ) as response:

                body = (
                    response
                    .read()
                    .decode(
                        "utf-8"
                    )
                )


        except (
            HTTPError,
            URLError,
            TimeoutError
        ) as exc:

            raise RuntimeError(
                "CFTC PRE request failed."
            ) from exc


        try:

            payload = json.loads(
                body
            )

        except json.JSONDecodeError as exc:

            raise RuntimeError(
                "CFTC PRE returned "
                "invalid JSON."
            ) from exc


        if not isinstance(
            payload,
            list
        ):

            raise RuntimeError(
                "CFTC PRE response "
                "must be a list."
            )


        return payload


    def _require(
        self,
        row,
        key
    ):

        if (
            key not in row
            or row[key] is None
            or str(
                row[key]
            ).strip() == ""
        ):

            raise ValueError(
                "CFTC row missing "
                f"required field: {key}"
            )


        return row[key]


    def _parse_int(
        self,
        row,
        key
    ):

        value = self._require(
            row,
            key
        )


        try:

            parsed = int(
                str(
                    value
                ).strip()
            )

        except (
            TypeError,
            ValueError
        ) as exc:

            raise ValueError(
                "Invalid integer CFTC field: "
                f"{key}={value!r}"
            ) from exc


        if parsed < 0:

            raise ValueError(
                "CFTC position fields "
                "cannot be negative: "
                f"{key}"
            )


        return parsed


    def _parse_report_date(
        self,
        row
    ):

        raw = str(

            self._require(
                row,
                "report_date_as_yyyy_mm_dd"
            )

        ).strip()


        try:

            return (
                datetime
                .fromisoformat(
                    raw
                )
                .date()
            )

        except ValueError as exc:

            raise ValueError(
                "Invalid CFTC "
                "report date."
            ) from exc


    def _parse_system_timestamp(
        self,
        row,
        key
    ):

        raw = str(

            self._require(
                row,
                key
            )

        ).strip()


        if raw.endswith(
            "Z"
        ):

            raw = (
                raw[:-1]
                + "+00:00"
            )


        try:

            value = (
                datetime
                .fromisoformat(
                    raw
                )
            )

        except ValueError as exc:

            raise ValueError(
                "Invalid Socrata system "
                f"timestamp: {key}"
            ) from exc


        if (
            value.tzinfo
            is None
        ):

            raise ValueError(
                "Socrata system timestamp "
                "must be timezone-aware: "
                f"{key}"
            )


        return value.astimezone(
            timezone.utc
        )


    def _normalize_contract_code(
        self,
        row
    ):

        raw = str(

            self._require(
                row,
                "cftc_contract_market_code"
            )

        ).strip()


        code = raw.zfill(
            6
        )


        if (
            code
            != (
                EURO_FX_CFTC_CONTRACT_MARKET_CODE
            )
        ):

            raise ValueError(
                "Unexpected CFTC contract "
                f"market code: {code}"
            )


        return code


    def _official_release_if_known(
        self,
        report_date
    ):

        try:

            return (
                self.release_calendar
                .available_at(
                    report_date
                )
            )

        except ValueError:

            # CFTC does not publish a complete
            # historical release-date calendar.
            # The PRE system timestamps remain
            # an official, conservative
            # availability bound.
            return None


    def _resolve_available_at(
        self,
        row,
        report_date
    ):

        created_at = (
            self._parse_system_timestamp(
                row,
                ":created_at"
            )
        )

        updated_at = (
            self._parse_system_timestamp(
                row,
                ":updated_at"
            )
        )


        if (
            updated_at
            < created_at
        ):

            raise ValueError(
                "CFTC PRE updated_at "
                "precedes created_at."
            )


        candidates = [
            created_at,
            updated_at
        ]


        official_release = (
            self._official_release_if_known(
                report_date
            )
        )


        if (
            official_release
            is not None
        ):

            candidates.append(
                official_release
            )


        # Conservative rule:
        #
        # Never permit this exact current row
        # before the latest authoritative
        # timestamp associated with it.
        return max(
            candidates
        )


    def _content_hash(
        self,
        payload
    ):

        encoded = json.dumps(

            payload,

            sort_keys=True,

            separators=(
                ",",
                ":"
            ),

            ensure_ascii=True
        ).encode(
            "utf-8"
        )


        return (
            hashlib
            .sha256(
                encoded
            )
            .hexdigest()
        )


    def normalize_row(
        self,
        row,
        ingested_at
    ):

        if not isinstance(
            row,
            dict
        ):

            raise TypeError(
                "CFTC row must be dict."
            )


        if (
            not isinstance(
                ingested_at,
                datetime
            )
            or ingested_at.tzinfo
            is None
        ):

            raise ValueError(
                "ingested_at must be "
                "timezone-aware datetime."
            )


        report_family = str(

            self._require(
                row,
                "futonly_or_combined"
            )

        ).strip()


        if (
            report_family
            != "FutOnly"
        ):

            raise ValueError(
                "CFTC row is not "
                "Futures Only."
            )


        contract_code = (
            self._normalize_contract_code(
                row
            )
        )


        report_date = (
            self._parse_report_date(
                row
            )
        )


        normalized = {

            "report_type":
                (
                    COT_REPORT_TYPE_TFF_FUTURES_ONLY
                ),

            "cftc_contract_market_code":
                contract_code,

            "market_and_exchange_names":
                str(
                    self._require(
                        row,
                        "market_and_exchange_names"
                    )
                ).strip(),

            "contract_market_name":
                str(
                    self._require(
                        row,
                        "contract_market_name"
                    )
                ).strip(),

            "commodity_name":
                str(
                    self._require(
                        row,
                        "commodity_name"
                    )
                ).strip(),

            "report_date":
                report_date,

            "open_interest":
                self._parse_int(
                    row,
                    "open_interest_all"
                ),

            "dealer_long":
                self._parse_int(
                    row,
                    "dealer_positions_long_all"
                ),

            "dealer_short":
                self._parse_int(
                    row,
                    "dealer_positions_short_all"
                ),

            "dealer_spread":
                self._parse_int(
                    row,
                    "dealer_positions_spread_all"
                ),

            "asset_manager_long":
                self._parse_int(
                    row,
                    "asset_mgr_positions_long"
                ),

            "asset_manager_short":
                self._parse_int(
                    row,
                    "asset_mgr_positions_short"
                ),

            "asset_manager_spread":
                self._parse_int(
                    row,
                    "asset_mgr_positions_spread"
                ),

            "leveraged_money_long":
                self._parse_int(
                    row,
                    "lev_money_positions_long"
                ),

            "leveraged_money_short":
                self._parse_int(
                    row,
                    "lev_money_positions_short"
                ),

            "leveraged_money_spread":
                self._parse_int(
                    row,
                    "lev_money_positions_spread"
                ),

            "other_reportables_long":
                self._parse_int(
                    row,
                    "other_rept_positions_long"
                ),

            "other_reportables_short":
                self._parse_int(
                    row,
                    "other_rept_positions_short"
                ),

            "other_reportables_spread":
                self._parse_int(
                    row,
                    "other_rept_positions_spread"
                ),

            "nonreportable_long":
                self._parse_int(
                    row,
                    "nonrept_positions_long_all"
                ),

            "nonreportable_short":
                self._parse_int(
                    row,
                    "nonrept_positions_short_all"
                )
        }


        # Only economic/source content enters
        # the hash.
        #
        # Socrata system timestamps and version
        # metadata deliberately do NOT enter the
        # hash because a dataset-wide replacement
        # may touch metadata without changing the
        # underlying COT observation.
        hash_payload = {

            key: (
                value.isoformat()
                if isinstance(
                    value,
                    date
                )
                else value
            )

            for key, value
            in normalized.items()
        }


        content_hash = (
            self._content_hash(
                hash_payload
            )
        )


        return COTPositioningSnapshot(

            **normalized,

            available_at=(
                self._resolve_available_at(
                    row=row,
                    report_date=report_date
                )
            ),

            source_record_id=str(

                self._require(
                    row,
                    "id"
                )

            ).strip(),

            content_hash=
                content_hash,

            source="CFTC",

            ingested_at=
                ingested_at
        )


    def normalize_rows(
        self,
        rows,
        ingested_at
    ):

        snapshots = tuple(

            self.normalize_row(
                row=row,
                ingested_at=ingested_at
            )

            for row
            in rows
        )


        return tuple(

            sorted(

                snapshots,

                key=lambda item: (
                    item.report_date,
                    item.available_at,
                    item.content_hash
                )
            )
        )


    def fetch_snapshots(
        self,
        start_report_date,
        end_report_date,
        ingested_at,
        limit=50000
    ):

        rows = self.fetch_rows(

            start_report_date=
                start_report_date,

            end_report_date=
                end_report_date,

            limit=
                limit
        )


        return self.normalize_rows(
            rows=rows,
            ingested_at=ingested_at
        )