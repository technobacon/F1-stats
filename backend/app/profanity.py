"""Username profanity / slur screening.

Keeps a curated blocklist of obscene words and slurs and a single
``contains_profanity`` check used when an account is created, so players can't
register offensive display names that then surface on the public leaderboard and
Constructors' Championship.

The word set is a pragmatic subset of the widely-used "List of Dirty, Naughty,
Obscene, and Otherwise Bad Words" (LDNOOBW), the same dictionary many products
and CDNs ship for English profanity filtering.

Matching is leet-aware: the candidate is lower-cased and common letter/number
substitutions are folded (0->o, 1->i, 3->e, 4->a, 5->s, 7->t, 8->b, @->a, $->s).
To balance catching evasions against the "Scunthorpe problem" (a clean word that
merely contains a rude one), there are two tiers:

  * STRONG words match anywhere, even across separators, after punctuation is
    stripped — so "f.u.c.k", "sh1t" and "a$$hole" are all caught. These are long
    or distinctive enough to rarely appear inside an innocent word.
  * SHORT words (ass, sex, cum, tit, …) match ONLY as a whole separator-delimited
    token, so "ass" / "ass_hat" are blocked but "class", "passenger", "sextet"
    and "competition" are not.
"""

from __future__ import annotations

import re

# Long / distinctive obscenities and slurs: matched as a substring of the
# punctuation-stripped, leet-folded candidate.
STRONG_WORDS: frozenset[str] = frozenset({
    "asshole", "bastard", "bitch", "blowjob", "bollock", "bollok", "buttplug",
    "clit", "cocksucker", "cunt", "dildo", "ejaculate", "faggot", "fellatio",
    "felching", "fuck", "fucker", "fucking", "gangbang", "handjob", "hentai",
    "jerkoff", "jizz", "masturbate", "molest", "motherfucker", "nigga", "nigger",
    "nutsack", "orgasm", "pussy", "rapist", "rectum", "rimjob", "scrotum",
    "shit", "shite", "smegma", "spunk", "testicle", "titties", "tosser",
    "vagina", "wanker", "whore", "wetback", "tranny",
})

# Short / collision-prone words: matched only as a standalone token (split on the
# username's '.', '_' and '-' separators), never as an inner substring.
SHORT_WORDS: frozenset[str] = frozenset({
    "anal", "anus", "arse", "ass", "boner", "boob", "chink", "cock", "coon",
    "cum", "dick", "dyke", "fag", "gook", "hooker", "horny", "jap", "kike",
    "knob", "milf", "nazi", "paki", "penis", "piss", "porn", "prick", "pube",
    "queer", "rape", "retard", "semen", "sex", "slut", "spic",
    "tit", "turd", "twat", "wank",
})

# Common visual letter/number substitutions, folded before matching.
_LEET = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
    "7": "t", "8": "b", "9": "g", "@": "a", "$": "s", "!": "i",
})


def contains_profanity(text: str) -> bool:
    """True if the username contains a blocked word (see the tiers above)."""
    folded = (text or "").lower().translate(_LEET)
    # Strong words: scan with separators/punctuation removed so spacers can't hide them.
    compact = "".join(ch for ch in folded if ch.isalnum())
    if any(word in compact for word in STRONG_WORDS):
        return True
    # Short words: only as whole tokens, so innocent words that merely contain them
    # (class, passenger, sextet, …) are left alone.
    tokens = [t for t in re.split(r"[^a-z0-9]+", folded) if t]
    return any(t in SHORT_WORDS for t in tokens)
