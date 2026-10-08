import hashlib
import json

from datetime import (
    datetime,
    timezone
)

from backend.macro.ecb_calendar_protocol import (
    ECB_2026_CALENDAR_AVAILABLE_AT,
    ECB_2026_POLICY_MEETINGS,
    ECB_CALENDAR_SOURCE,
    ECB_POLICY_EVENT_CONTENT_HASH_FIELDS,
    ECB_POLICY_EVENT_CURRENCY,
    ECB_POLICY_EVENT_JURISDICTION,
    ECB_POLICY_EVENT_NAME,
    ECBPolicyMeetingSpec,
    build_ecb_decision_scheduled_at,
    build_ecb_policy_event_id
)

from backend.macro.macro_schemas import (
    EconomicCalendarEventSnapshot
)


class ECBPolicyCalendarBuilder:

    def _validate_datetime(
        self,
        value,
        name
    ):

        if not isinstance(
            value,
            datetime
        ):

            raise TypeError(
                f"{name} must be datetime."
            )


        if (
            value.tzinfo
            is None
        ):

            raise ValueError(
                f"{name} must be "
                "timezone-aware."
            )


    def _content_hash(
        self,
        payload
    ):

        if (
            tuple(
                payload.keys()
            )
            != (
                ECB_POLICY_EVENT_CONTENT_HASH_FIELDS
            )
        ):

            raise ValueError(
                "ECB calendar hash payload "
                "does not match frozen "
                "protocol."
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


    def build_snapshot(
        self,
        spec,
        ingested_at,
        available_at=(
            ECB_2026_CALENDAR_AVAILABLE_AT
        )
    ):

        if not isinstance(
            spec,
            ECBPolicyMeetingSpec
        ):

            raise TypeError(
                "spec must be "
                "ECBPolicyMeetingSpec."
            )


        self._validate_datetime(
            ingested_at,
            "ingested_at"
        )


        self._validate_datetime(
            available_at,
            "available_at"
        )


        ingested_at = (
            ingested_at.astimezone(
                timezone.utc
            )
        )


        available_at = (
            available_at.astimezone(
                timezone.utc
            )
        )


        source_event_id = (
            build_ecb_policy_event_id(

                year=
                    spec.year,

                ordinal=
                    spec.ordinal
            )
        )


        scheduled_at = (
            build_ecb_decision_scheduled_at(
                spec.meeting_date
            )
        )


        hash_payload = {

            "source_event_id":
                source_event_id,

            "event_name":
                ECB_POLICY_EVENT_NAME,

            "jurisdiction":
                ECB_POLICY_EVENT_JURISDICTION,

            "currency":
                ECB_POLICY_EVENT_CURRENCY,

            "scheduled_at":
                scheduled_at.isoformat(),

            "actual":
                None,

            "forecast":
                None,

            "previous":
                None,

            "unit":
                None,

            "source":
                ECB_CALENDAR_SOURCE
        }


        content_hash = (
            self._content_hash(
                hash_payload
            )
        )


        return EconomicCalendarEventSnapshot(

            source_event_id=
                source_event_id,

            event_name=
                ECB_POLICY_EVENT_NAME,

            jurisdiction=
                ECB_POLICY_EVENT_JURISDICTION,

            currency=
                ECB_POLICY_EVENT_CURRENCY,

            scheduled_at=
                scheduled_at,

            available_at=
                available_at,

            actual=None,

            forecast=None,

            previous=None,

            unit=None,

            content_hash=
                content_hash,

            source=
                ECB_CALENDAR_SOURCE,

            ingested_at=
                ingested_at
        )


    def build_snapshots(
        self,
        specs,
        ingested_at,
        available_at=(
            ECB_2026_CALENDAR_AVAILABLE_AT
        )
    ):

        specs = tuple(
            specs
        )


        snapshots = tuple(

            self.build_snapshot(

                spec=
                    spec,

                ingested_at=
                    ingested_at,

                available_at=
                    available_at
            )

            for spec
            in specs
        )


        return tuple(

            sorted(

                snapshots,

                key=lambda item: (

                    item.scheduled_at,

                    item.source_event_id
                )
            )
        )


    def build_2026_snapshots(
        self,
        ingested_at
    ):

        return self.build_snapshots(

            specs=
                ECB_2026_POLICY_MEETINGS,

            ingested_at=
                ingested_at,

            available_at=(
                ECB_2026_CALENDAR_AVAILABLE_AT
            )
        )