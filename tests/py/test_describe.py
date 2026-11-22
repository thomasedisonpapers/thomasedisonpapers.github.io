"""A document's description is drawn from the letter itself, not its letterhead, address block or stamps."""
import describe

LETTERHEAD = """[Letterhead] Cie CONTINENTALE EDISON
SOCIÉTÉ ANONYME
Au Capital de 1 Million de Francs

Paris, le 5 Février 1888

Edison Electric Light Co of Europe
New York.

Gentlemen,

We confirm our respects of the 31st inst & wired today to Mr. Edison as follows.
"Without answer cable desjo 96 proceeding delayed."
"""


def record(text, description):
    return {"transcription": text, "description": description}


def test_a_description_skips_the_letterhead_regression_ce85_f_repeated_descriptions_22_november():
    letter = record(LETTERHEAD, "Cie CONTINENTALE EDISON SOCIÉTÉ ANONYME Au Capital de 1 Million de Francs.")
    assert describe.describe(letter) == "We confirm our respects of the 31st inst & wired today to Mr. Edison as follows."


def test_a_description_skips_an_untagged_address_block_before_the_salutation():
    text = ("Mr. Bruce Barton,\nBatten, Barton, Durstine & Osborn,\n383 Madison Avenue,\nNew York City, N. Y.\n\nMy dear Mr. Barton:\n\n"
            "     I take pleasure in sending you a copy of the brochure recently issued by the Foundation, setting forth its aims.")
    assert describe.opening_sentence(text).startswith("I take pleasure")


def test_long_small_print_inside_a_letterhead_is_skipped_with_it():
    text = ("[Letterhead] Armat Motion Picture Company\n     Owners of Patents Covering all Projecting Machines in use in this Country, "
            "and also of the business of the American Kinetoscope Co., and the N. Y. Photo-Projecting Company, American Patents.\n\n"
            "     Resolved: That the Board of Directors approve the proposed contract with the Edison Manufacturing Company.")
    assert describe.opening_sentence(text).startswith("Resolved")


def test_a_stamp_as_long_as_a_paragraph_is_kept_as_content():
    notice = ("[Stamp] THE ANNUAL MEETING of the Stockholders of the Edison Electric Illuminating Company of New York, for the "
              "election of Thirteen Directors to serve for the ensuing year, will be held at the Office of the Company.")
    assert describe.opening_sentence(notice).startswith("THE ANNUAL MEETING")


def test_a_salutation_on_a_later_page_does_not_hide_the_first_clipping():
    text = ("     To the Editor: If Mr. Edison succeeds in proving by science that there is a future life, his next step will be "
            "to prove a way to get it.\n\n[page 2]\n\nDear Sir,\n\n     A judge hits a minister over a sermon that he heard last Sunday.")
    assert describe.opening_sentence(text).startswith("To the Editor")


def test_a_sentence_runs_past_a_month_abbreviation():
    text = "Gentlemen:-\n\n     Your 1919 calendar was good when you got it but on Jan. 1st it will be out of date and dear at any price."
    assert describe.opening_sentence(text).endswith("dear at any price.")


def test_notebook_prose_written_in_short_lines_still_counts_as_prose():
    text = "I calculated that 3 washings\nwould extract practically\nall Nickel from the mud,\nso that a filter press is not needed."
    assert describe.opening_sentence(text).startswith("I calculated")


def test_a_catalogue_abstract_is_kept_as_it_is():
    abstract = "Edison asks the company for an account of the lamps shipped to Paris."
    assert describe.describe(record(LETTERHEAD, abstract)) == abstract


def test_a_description_is_kept_when_the_body_has_no_prose():
    text = "[Letterhead] WESTERN UNION\n\nRECEIVED AT\nNEW YORK"
    assert describe.describe(record(text, "Western Union.")) == "Western Union."
