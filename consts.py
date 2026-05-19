"""Various constants.

This module is for constants that we use across the application. Constants that
are more locally scoped should be defined in the modules that use them.
"""

from dataclasses import dataclass


@dataclass
class Locale:
    """Represents a locale option that we expose to users.

    Requirements:
    - `code` must correspond to a language listed in our Transifex project [1].
    - Translation data should cover, at a minimum, all of the core UI text on
      Kalanjiyam.

    [1]: https://www.transifex.com/kalanjiyam/kalanjiyam
    """

    #: The full locale for this code, e.g. "hi_IN"
    code: str
    #: The locale as it appears in the URL, e.g. "hi". This is just a
    #: simpliifed version of `code`. We use `slug` over `code` for a simpler
    #: user experience.
    slug: str
    #: The human-readable name of this language. We follow the convention of
    #: sites like Wikipedia and use a name that a native speaker of the
    #: language would use and prefer, thus "Italiano" and not "Italian."
    text: str


#: Defines a rough taxonomy of texts.
#:
#: This taxonomy is a temporary measure, and soon we will move this data into
#: the database and avoid hard-coding a lists of texts.
TEXT_CATEGORIES = {
    "medicine": [
        "siddha_medicine",
        "herbal_remedies",
        "therapeutic_formulas",
    ],
    "alchemy": [
        "rasa_shastra",
        "mercury_processing",
        "mineral_medicines",
    ],
    "yoga": [
        "siddha_yoga",
        "kundalini_practices",
        "breathing_techniques",
    ],
    "philosophy": [
        "siddha_philosophy",
        "spiritual_wisdom",
        "cosmology",
    ],
    "literature": [
        "tamil_poetry",
        "devotional_texts",
        "historical_accounts",
    ],
}


#: The username for our internal bot user.

#: `kalanjiyam-bot` performs background tasks like OCR. We assign these tasks to a
#: bot user so that we can better separate automatic work from work done
#: manually.
BOT_USERNAME = "kalanjiyam-bot"


#: All of the locales we support on Kalanjiyam.
#:
#: We render this list of locales on the main page and in page footers. As this
#: list grows, we can consider more manageable ways to present this data to the
#: user.
LOCALES = [
    Locale(code="ta", slug="ta", text="தமிழ்"),
    Locale(code="en", slug="en", text="English"),
    Locale(code="hi_IN", slug="hi", text="हिन्दी"),
    Locale(code="sa", slug="sa", text="संस्कृतम्"),
    Locale(code="te_IN", slug="te", text="తెలుగు"),
]
