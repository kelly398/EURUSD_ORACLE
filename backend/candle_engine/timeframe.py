from datetime import timedelta


def floor_time(
    timestamp,
    minutes
):

    return timestamp.replace(
        minute=(
            timestamp.minute
            - timestamp.minute % minutes
        ),
        second=0,
        microsecond=0
    )


def previous_window(
    timestamp,
    minutes
):

    current_boundary = floor_time(
        timestamp,
        minutes
    )

    start = (
        current_boundary
        - timedelta(minutes=minutes)
    )

    end = current_boundary

    return start, end
    