Internationalization and Localization (i18n & l10n)
===================================================

Internationalization and localization (**i18n and l10n**) allow Kalanjiyam to present its entire user interface in multiple Indian languages as well as English.

Supported Locales
-----------------

Kalanjiyam natively supports 5 locales configured in ``kalanjiyam.consts.LOCALES``:

+------------+-----------+----------------------+-----------------------------+
| Locale     | Slug      | Native Display Name  | Language                    |
+============+===========+======================+=============================+
| ``en``     | ``en``    | English              | English (Default Source)    |
+------------+-----------+----------------------+-----------------------------+
| ``ta``     | ``ta``    | தமிழ்                | Tamil                       |
+------------+-----------+----------------------+-----------------------------+
| ``hi_IN``  | ``hi``    | हिन्दी               | Hindi                       |
+------------+-----------+----------------------+-----------------------------+
| ``sa``     | ``sa``    | संस्कृतम्             | Sanskrit                    |
+------------+-----------+----------------------+-----------------------------+
| ``te_IN``  | ``te``    | తెలుగు               | Telugu                      |
+------------+-----------+----------------------+-----------------------------+

Catalogs are stored under ``kalanjiyam/translations/<locale>/LC_MESSAGES/``:
* ``messages.po``: Human-readable translation catalogs.
* ``messages.mo``: Compiled binary catalogs loaded by Flask-Babel and Gettext.

-------------------------------------------------------------------------------

1. Annotating Translatable Strings in Code
------------------------------------------

We manage i18n through `Flask-Babel`_. Strings are marked in Python and Jinja templates using standard Gettext functions:

In Jinja Templates:
~~~~~~~~~~~~~~~~~~~
.. code-block:: jinja

    <!-- Simple string -->
    {{ _('Proofreading') }}

    <!-- Pluralized string -->
    {{ ngettext('%(num)d page', '%(num)d pages', count) }}

    <!-- Context-specific string -->
    {{ pgettext('proofing_page', 'Save Revision') }}

In Python Code:
~~~~~~~~~~~~~~~
.. code-block:: python

    from flask_babel import _, _l, pgettext

    # Lazy string for forms / model definitions
    label = _l("Project Name")

    # Runtime translation
    flash(_("Your changes have been saved successfully."))

.. _Flask-Babel: https://python-babel.github.io/flask-babel/

-------------------------------------------------------------------------------

2. Extracting & Initializing Catalogs
-------------------------------------

To extract all translatable strings from the codebase and initialize/update the catalog files:

.. code-block:: bash

    # Run via Makefile
    make init-i18n

    # Or run the script directly
    python -m kalanjiyam.scripts.fetch_i18n_files

What this script does:
1. **Extracts** translatable strings from all ``.py`` and ``.html`` files into ``messages.pot`` using ``babel.cfg``.
2. **Initializes** missing locale directories and catalogs (``ta``, ``hi_IN``, ``sa``, ``te_IN``, ``en``).
3. **Updates** existing ``messages.po`` files with newly added strings without overwriting existing translations.
4. **Compiles** all catalogs to ``messages.mo``.

-------------------------------------------------------------------------------

3. Machine Translation via LLM Backends
----------------------------------------

Kalanjiyam includes an automated translation pipeline ([`kalanjiyam/scripts/translate_catalogs.py`](file:///home/mrportable/Documents/kalanjiyam/kalanjiyam/scripts/translate_catalogs.py)) powered by LLM translation engines.

Translation Engines:
~~~~~~~~~~~~~~~~~~~~
* **``llm_gemma``** (*Default*): Uses the 26B instruction-tuned ``llm-gemma`` model hosted on the OCR backend (``OCR_SERVICE_URL`` / ``/v1/ocr`` or ``/v1/chat/completions``).
* **``gemma``**: Uses the multilingual ``google/gemma-4-12b-it`` model hosted on ``TRANSLATION_SERVICE_URL``.
* **``indictrans2``**: Uses the 1B IndicTrans2 models.
* **``bharatgen``**: Connects to the BharatGen API.

Basic Translation Command:
~~~~~~~~~~~~~~~~~~~~~~~~~~
.. code-block:: bash

    # Translate with llm-gemma (default)
    python -m kalanjiyam.scripts.translate_catalogs --engine llm_gemma

    # Or translate with gemma 12B
    python -m kalanjiyam.scripts.translate_catalogs --engine gemma

Incremental & Resume Behavior (Default):
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
By default, the script **only translates missing, empty, or failed strings**.
* If a string already has a translated ``msgstr``, it is automatically skipped.
* If a translation process is cancelled (Ctrl+C) or interrupted by a network glitch, running the command again will safely resume from where it left off.
* The script features graceful interruption handling: pressing **Ctrl+C** automatically saves all translated entries and compiles ``messages.mo`` before exiting.

Forcing Full Re-Translation (``--force``):
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
To overwrite and re-translate all strings from scratch:

.. code-block:: bash

    python -m kalanjiyam.scripts.translate_catalogs --engine llm_gemma --force

Command-Line Options:
~~~~~~~~~~~~~~~~~~~~~
.. code-block:: text

    --engine ENGINE        Translation backend (llm_gemma, gemma, bharatgen, google, openai, generic)
    --locales LOC ...      Locales to translate (default: ta hi_IN sa te_IN)
    --batch-size N         Batch size for translation requests (default: 20)
    --force, -f            Force re-translation of all strings (overwrites existing msgstr)
    --dry-run              Scan and report untranslated strings without translating
    --api-url URL          Override backend API endpoint
    --api-key KEY          Override API authorization key
    --no-compile           Skip automatic .mo compilation after translation

-------------------------------------------------------------------------------

4. Docker & Remote Server Execution
-----------------------------------

When running on staging or production containers (e.g. ``kalanjiyam-web-staging``):

Running the Translation in Container:
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
.. code-block:: bash

    # Run translation inside container with proxy bypass
    NO_PROXY="*" docker exec -it kalanjiyam-web-staging python -m kalanjiyam.scripts.translate_catalogs --engine llm_gemma

Syncing Translated Catalogs to Host:
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
To extract the translated catalogs from the container to your host repository without needing ``sudo`` permissions:

.. code-block:: bash

    cd ~/kalanjiyam-dev/kalanjiyam
    docker exec kalanjiyam-web-staging tar -C /app/kalanjiyam -cf - translations | tar -xf -

Compiling & Reloading Translations in Web App:
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Flask-Babel and Gunicorn cache compiled ``.mo`` files in memory. After updating catalogs:

.. code-block:: bash

    # 1. Compile .po to .mo
    docker exec -it kalanjiyam-web-staging pybabel compile -d kalanjiyam/translations

    # 2. Restart web container to reload memory cache
    docker restart kalanjiyam-web-staging

-------------------------------------------------------------------------------

5. Adding a New Locale
----------------------

To add a new language to Kalanjiyam:

1. Add the locale to ``LOCALES`` in ``kalanjiyam/consts.py``:

   .. code-block:: python

       LOCALES = [
           Locale(code="ta", slug="ta", text="தமிழ்"),
           Locale(code="en", slug="en", text="English"),
           Locale(code="hi_IN", slug="hi", text="हिन्दी"),
           Locale(code="sa", slug="sa", text="संस्कृतम्"),
           Locale(code="te_IN", slug="te", text="తెలుగు"),
           Locale(code="bn_IN", slug="bn", text="বাংলা"),  # New locale
       ]

2. Initialize the catalog for the new locale:

   .. code-block:: bash

       pybabel init -i messages.pot -d kalanjiyam/translations -l bn_IN

3. Translate the new catalog using the translation engine:

   .. code-block:: bash

       python -m kalanjiyam.scripts.translate_catalogs --locales bn_IN --engine llm_gemma

4. Compile the catalogs:

   .. code-block:: bash

       pybabel compile -d kalanjiyam/translations
