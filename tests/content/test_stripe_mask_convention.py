"""Destination stripe-mask convention for imported slime body materials.

The SR1 native `SR/AMP/Slime/Body/Stripe` shader reads `_StripeTexture` with the
destination convention: BRIGHT (white) = plain body, DARK = the marked/striped
region. The shipped SR1 masks prove the range: `body_stripes` 94.5% white with a
179 floor (Tabby, subtle) up to `body_stripes_hunter` with a 0 floor (strong).

The SR2 source `_ColorMask` stores the opposite (bright = marking: R = dark
markings incl. ear pattern, B = tail ring bands), so the destination mask must be
the inverted maximum of those channels. A previous build wrote `max(R,B)`
directly, which made the body render the stripe treatment almost everywhere and
the real markings untreated - the reported wrong color patches.
"""
import numpy as np

from bridge.content.sr2_asset_extract import destination_stripe_mask


def test_marks_are_inverted_into_the_native_bright_body_convention():
    red = np.array([255, 0, 0, 10], dtype=np.float64)
    blue = np.array([0, 0, 255, 10], dtype=np.float64)
    mask = destination_stripe_mask(red, blue)
    assert mask.tolist() == [0.0, 255.0, 0.0, 245.0]
    # The union of the two mark channels is what gets darkened; a fully unmarked
    # source region stays white (plain body), never black.
    assert destination_stripe_mask(np.zeros(4), np.zeros(4)).tolist() == [255.0] * 4
    # Regression guard: the shipped inverse of the correct mask is NOT produced.
    assert not np.array_equal(mask, np.maximum(red, blue))
