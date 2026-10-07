import math

from datetime import (
    date,
    datetime
)

from statistics import (
    fmean,
    pstdev
)

from pydantic import (
    BaseModel,
    ConfigDict,
    model_validator
)

from backend.institutional.cot_research_protocol import (
    CONTEXT_FEATURES,
    DIRECTIONAL_CANDIDATE_FEATURES,
    FEATURE_VERSION,
    MINIMUM_COT_HISTORY_REPORTS,
    ROLLING_REFERENCE_REPORTS
)

from backend.institutional.cot_schemas import (
    COTPositioningSnapshot
)


class COTFeatureVector(BaseModel):

    model_config = ConfigDict(
        frozen=True
    )

    feature_version: str

    report_date: date

    available_at: datetime

    feature_names: tuple[
        str,
        ...
    ]

    values: tuple[
        float,
        ...
    ]


    @model_validator(
        mode="after"
    )
    def validate_vector(self):

        if (
            self.available_at.tzinfo
            is None
        ):

            raise ValueError(
                "COT feature available_at "
                "must be timezone-aware."
            )


        if not self.feature_names:

            raise ValueError(
                "COT feature vector "
                "cannot be empty."
            )


        if (
            len(
                self.feature_names
            )
            != len(
                self.values
            )
        ):

            raise ValueError(
                "COT feature names and "
                "values must have equal "
                "length."
            )


        if (
            len(
                set(
                    self.feature_names
                )
            )
            != len(
                self.feature_names
            )
        ):

            raise ValueError(
                "COT feature names "
                "must be unique."
            )


        for value in self.values:

            if not math.isfinite(
                value
            ):

                raise ValueError(
                    "COT feature values "
                    "must be finite."
                )


        return self


    def as_dict(self):

        return dict(
            zip(
                self.feature_names,
                self.values
            )
        )


class COTFeatureBuilder:

    FEATURE_VERSION = (
        FEATURE_VERSION
    )

    FEATURE_NAMES = (
        DIRECTIONAL_CANDIDATE_FEATURES
        + CONTEXT_FEATURES
    )


    def _validate_history(
        self,
        cot_data
    ):

        history = tuple(
            cot_data
        )


        if (
            len(
                history
            )
            < MINIMUM_COT_HISTORY_REPORTS
        ):

            raise ValueError(
                "COT feature construction "
                "requires at least "
                f"{MINIMUM_COT_HISTORY_REPORTS} "
                "causal reports."
            )


        for item in history:

            if not isinstance(
                item,
                COTPositioningSnapshot
            ):

                raise TypeError(
                    "COT feature history must "
                    "contain only "
                    "COTPositioningSnapshot."
                )


        for previous, current in zip(
            history,
            history[1:]
        ):

            if (
                current.report_date
                <= previous.report_date
            ):

                raise ValueError(
                    "COT feature history must "
                    "be strictly chronological "
                    "by report_date."
                )


        first_report_type = (
            history[0]
            .report_type
        )

        first_contract_code = (
            history[0]
            .cftc_contract_market_code
        )


        for item in history:

            if (
                item.report_type
                != first_report_type
            ):

                raise ValueError(
                    "COT feature history "
                    "contains mixed report "
                    "types."
                )


            if (
                item
                .cftc_contract_market_code
                != first_contract_code
            ):

                raise ValueError(
                    "COT feature history "
                    "contains mixed contract "
                    "market codes."
                )


        # Only the current observation and
        # its prior 52-report reference
        # history are required by v1.
        relevant_history = (
            history[
                -MINIMUM_COT_HISTORY_REPORTS:
            ]
        )


        for item in relevant_history:

            if (
                item.open_interest
                <= 0
            ):

                raise ValueError(
                    "COT feature construction "
                    "requires strictly positive "
                    "open interest."
                )


        return history


    def _net_share(
        self,
        item,
        long_field,
        short_field
    ):

        long_value = getattr(
            item,
            long_field
        )

        short_value = getattr(
            item,
            short_field
        )


        return (
            float(
                long_value
                - short_value
            )
            / float(
                item.open_interest
            )
        )


    def _asset_manager_net_share(
        self,
        item
    ):

        return self._net_share(
            item=item,
            long_field=(
                "asset_manager_long"
            ),
            short_field=(
                "asset_manager_short"
            )
        )


    def _leveraged_money_net_share(
        self,
        item
    ):

        return self._net_share(
            item=item,
            long_field=(
                "leveraged_money_long"
            ),
            short_field=(
                "leveraged_money_short"
            )
        )


    def _dealer_net_share(
        self,
        item
    ):

        return self._net_share(
            item=item,
            long_field=(
                "dealer_long"
            ),
            short_field=(
                "dealer_short"
            )
        )


    def _z_score_against_reference(
        self,
        current_value,
        reference_values,
        feature_name
    ):

        reference_values = tuple(
            float(
                value
            )
            for value
            in reference_values
        )


        if (
            len(
                reference_values
            )
            != ROLLING_REFERENCE_REPORTS
        ):

            raise ValueError(
                f"{feature_name} requires "
                f"exactly "
                f"{ROLLING_REFERENCE_REPORTS} "
                "prior reference reports."
            )


        reference_mean = (
            fmean(
                reference_values
            )
        )

        reference_std = (
            pstdev(
                reference_values
            )
        )


        if (
            reference_std
            <= 0.0
        ):

            raise ValueError(
                f"{feature_name} cannot be "
                "constructed because the "
                "52-report reference window "
                "has zero variance."
            )


        return (
            float(
                current_value
                - reference_mean
            )
            / float(
                reference_std
            )
        )


    def build(
        self,
        cot_data
    ):

        history = (
            self._validate_history(
                cot_data
            )
        )


        current = (
            history[-1]
        )

        previous = (
            history[-2]
        )

        four_reports_ago = (
            history[-5]
        )


        current_asset = (
            self._asset_manager_net_share(
                current
            )
        )

        previous_asset = (
            self._asset_manager_net_share(
                previous
            )
        )

        asset_four_ago = (
            self._asset_manager_net_share(
                four_reports_ago
            )
        )


        current_leveraged = (
            self._leveraged_money_net_share(
                current
            )
        )

        previous_leveraged = (
            self._leveraged_money_net_share(
                previous
            )
        )

        leveraged_four_ago = (
            self._leveraged_money_net_share(
                four_reports_ago
            )
        )


        current_dealer = (
            self._dealer_net_share(
                current
            )
        )


        previous_open_interest = (
            float(
                previous.open_interest
            )
        )


        open_interest_change_1 = (

            float(
                current.open_interest
                - previous.open_interest
            )

            / previous_open_interest
        )


        # ==================================
        # PRIOR 52-REPORT REFERENCE WINDOW
        # ==================================
        #
        # The current report is explicitly
        # excluded from its own reference
        # distribution.
        # ==================================

        reference_history = (
            history[
                -(
                    ROLLING_REFERENCE_REPORTS
                    + 1
                ):
                -1
            ]
        )


        assert (
            len(
                reference_history
            )
            == ROLLING_REFERENCE_REPORTS
        )


        asset_reference = tuple(

            self._asset_manager_net_share(
                item
            )

            for item
            in reference_history
        )


        leveraged_reference = tuple(

            self._leveraged_money_net_share(
                item
            )

            for item
            in reference_history
        )


        asset_z52 = (
            self._z_score_against_reference(

                current_value=
                    current_asset,

                reference_values=
                    asset_reference,

                feature_name=(
                    "asset_manager_net_share_z52"
                )
            )
        )


        leveraged_z52 = (
            self._z_score_against_reference(

                current_value=
                    current_leveraged,

                reference_values=
                    leveraged_reference,

                feature_name=(
                    "leveraged_money_net_share_z52"
                )
            )
        )


        feature_values = {

            "asset_manager_net_share":
                current_asset,

            "leveraged_money_net_share":
                current_leveraged,

            "asset_manager_net_share_change_1":
                (
                    current_asset
                    - previous_asset
                ),

            "leveraged_money_net_share_change_1":
                (
                    current_leveraged
                    - previous_leveraged
                ),

            "asset_manager_net_share_change_4":
                (
                    current_asset
                    - asset_four_ago
                ),

            "leveraged_money_net_share_change_4":
                (
                    current_leveraged
                    - leveraged_four_ago
                ),

            "asset_manager_minus_leveraged_net_share":
                (
                    current_asset
                    - current_leveraged
                ),

            "dealer_net_share":
                current_dealer,

            "open_interest_change_1":
                open_interest_change_1,

            "asset_manager_net_share_z52":
                asset_z52,

            "leveraged_money_net_share_z52":
                leveraged_z52
        }


        values = tuple(

            float(
                feature_values[
                    feature_name
                ]
            )

            for feature_name
            in self.FEATURE_NAMES
        )


        return COTFeatureVector(

            feature_version=
                self.FEATURE_VERSION,

            report_date=
                current.report_date,

            available_at=
                current.available_at,

            feature_names=
                self.FEATURE_NAMES,

            values=
                values
        )