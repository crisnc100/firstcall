"""Normal forms for phones, emails and names, so the same buyer always compares equal."""

from __future__ import annotations

import re

import phonenumbers

# The local part allows every character RFC 5322 allows unquoted, so o'connor@ and
# buyer!sales@ are read whole. The look-behind stops a match from starting inside a longer
# local part, which would shorten one person's address into another's. Bounded counts keep
# each attempt short, so a scan is linear in the text.
LOCAL = r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]"
EMAIL = re.compile(
    rf"(?<!{LOCAL}){LOCAL}{{1,64}}@[A-Za-z0-9-]{{1,63}}(?:\.[A-Za-z0-9-]{{1,63}}){{0,8}}"
    r"\.[A-Za-z]{2,24}(?![A-Za-z0-9-])"
)
QUOTES = "'`"


def normalize_phone(text: str | None, region: str = "US") -> str | None:
    """E.164, or None when the text is not a valid number."""
    if not text or not text.strip():
        return None
    try:
        number = phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def find_phones(text: str, region: str = "US") -> list[str]:
    """Every valid phone in free text, as E.164, in the order they appear. The matcher's
    default leniency already drops numbers that are not valid."""
    return [
        phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
        for match in phonenumbers.PhoneNumberMatcher(text, region)
    ]


def normalize_email(text: str | None) -> str | None:
    if not text:
        return None
    found = find_emails(text)
    return found[0] if found else None


def find_emails(text: str) -> list[str]:
    """Every address in the text, lowercased, in order. An address is kept exactly as
    written, leading quote included, because 'ana@ and ana@ can be two people's addresses.
    The one exception is an address wrapped in a matching pair, as in 'ana@example.com',
    where the quotes are punctuation around it."""
    found: list[str] = []
    for match in EMAIL.finditer(text):
        address = match.group(0)
        first, after = address[0], text[match.end() : match.end() + 1]
        if first in QUOTES and after == first:
            address = address[1:]
        local = address.split("@", 1)[0]
        if local.strip(QUOTES):
            found.append(address.lower())
    return found


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def normalize_name(text: str | None) -> str | None:
    """Lowercased words separated by one space; None when nothing is left."""
    if not text:
        return None
    words = re.sub(r"[^\w'\- ]+", " ", text).split()
    return " ".join(words).casefold() or None


def first_name(name: str | None) -> str | None:
    normal = normalize_name(name)
    return normal.split(" ")[0] if normal else None
