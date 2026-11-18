import pytest

from src.db.studytomy.commerce import StudyOfferTerms


@pytest.mark.parametrize("kind", ["participation", "completion", "certificate"])
def test_described_awards_remain_draftable_but_block_sale(kind):
    terms = StudyOfferTerms.model_validate(
        {"award": {"kind": kind, "description": "Synthetic reviewed award proposal"}}
    )

    assert "OFFER_MANAGED_AWARD_UNAVAILABLE" in terms.completion_blockers()
    assert "OFFER_AWARD_DESCRIPTION_REQUIRED" not in terms.completion_blockers()


def test_explicit_no_award_does_not_block_sale():
    terms = StudyOfferTerms.model_validate({"award": {"kind": "none"}})

    assert "OFFER_MANAGED_AWARD_UNAVAILABLE" not in terms.completion_blockers()
    assert "OFFER_AWARD_TERMS_REQUIRED" not in terms.completion_blockers()
