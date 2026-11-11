"""Normal forms for phones, emails and names, so the same buyer always compares equal."""

from __future__ import annotations

import re

import phonenumbers

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


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
    match = EMAIL.search(text)
    return match.group(0).lower() if match else None


def find_emails(text: str) -> list[str]:
    return [m.lower() for m in EMAIL.findall(text)]


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
