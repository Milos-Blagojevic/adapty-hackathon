"""The title a person reads instead of a raw subject such as "Chat: refund"."""

from resolve.tickets import short_title, ticket_title


def test_a_subject_a_person_wrote_stays():
    assert ticket_title("Annual plan disappeared", "body", "The user lost the plan.") == "Annual plan disappeared"


def test_machine_subject_gives_way_to_the_summary():
    summary = "The user was charged $49.99 for the annual Google Play plan on Oct 19, no longer uses FitBuddy, and wants their money back."
    assert ticket_title("Chat: refund", "body", summary) == "Was charged $49.99 for the annual Google Play plan on Oct 19"


def test_machine_subject_without_triage_reads_as_the_reason():
    assert ticket_title("Chat: profile_mismatch", "body", None) == "Account does not match what the user says"
    assert ticket_title("Chat: something_new", "body", None) == "Handed off from chat"


def test_cut_of_the_body_gives_way_to_the_summary():
    body = "x" * 100
    assert ticket_title(body[:60], body, "The user wants a refund.") == "Wants a refund"


def test_short_title_keeps_dates_and_brackets_whole_and_stays_short():
    assert short_title("Paid on Nov 4, 2026 for the annual plan and it still shows as locked.") == "Paid on Nov 4, 2026 for the annual plan and it still shows as locked"
    long = "A Pro annual subscriber (paid $53.64, active to Oct 13, 2027) asks what the new label means and whether they have to pay again."
    out = short_title(long)
    assert len(out) <= 91 and out.endswith("…") and "(paid $53.64, active to Oct 13, 2027)" in out


def test_short_title_never_ends_on_a_joining_word():
    out = short_title("The user bought FitBuddy lifetime on iPhone and, after switching to a Pixel, the Android app asks them to pay again.")
    assert not out.endswith(" and") and out.startswith("Bought FitBuddy lifetime on iPhone and, after switching to a Pixel")
