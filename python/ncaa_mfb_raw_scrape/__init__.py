"""NCAA MFB raw-scrape library (discover/capture/parse/datasets/cfbfastr + run driver)."""


# --- coverage window -------------------------------------------------------
# `--all` used to mean `range(2014, 2027)` -- a HARDCODED ceiling that silently
# excluded ay2027 (= fall 2026) with no error. This is the same class of guard
# that burned a whole campaign in the WBB sibling repo: a stale MAX_SEASON does
# not fail loudly, it just quietly does less than you asked for.
#
# Floor is real and fixed: stats.ncaa.org has no box/pbp before ay2014.
COVERAGE_FLOOR_AY = 2014


def current_academic_year(today=None) -> int:
    """The academic year currently in progress (ay = season + 1).

    Fall 2026 is season 2026 = ay 2027. The NCAA year rolls in August, so
    Sep-2026 and Mar-2027 both resolve to ay2027.
    """
    import datetime as _dt

    today = today or _dt.date.today()
    return today.year + 1 if today.month >= 8 else today.year


def coverage_years(through=None) -> range:
    """Every academic year we could hold data for, floor..current inclusive."""
    return range(COVERAGE_FLOOR_AY, (through or current_academic_year()) + 1)
