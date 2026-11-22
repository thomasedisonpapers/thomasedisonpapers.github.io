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


def test_an_abstract_quoting_a_later_sentence_is_kept():
    abstract = "Please send me a full statement of all outstanding bills."
    text = "Our accounting office is preparing for the annual meeting and needs the ledgers from every branch.\n\n" + abstract
    assert describe.describe(record(text, abstract)) == abstract


def test_an_abstract_matching_a_stamp_on_a_later_page_is_kept():
    abstract = "Received during the ownership review of several outstanding deeds."
    text = ("I am writing to give you the latest figures on the factory tests and our new storage batteries."
            f"\n\n[page 2]\n\n[Stamp] {abstract}")
    assert describe.describe(record(text, abstract)) == abstract


def test_editors_notes_that_quote_the_transcription_are_kept():
    note = "Cie CONTINENTALE EDISON SOCIÉTÉ ANONYME Au Capital de 1 Million de Francs."
    assert describe.describe({**record(LETTERHEAD, note), "editors_notes": note}) == note


def test_a_harvested_abstract_is_kept_even_when_it_matches_the_letterhead():
    abstract = "Cie CONTINENTALE EDISON SOCIÉTÉ ANONYME Au Capital de 1 Million de Francs."
    assert describe.describe({**record(LETTERHEAD, abstract), "source": "edisondigital"}) == abstract


def test_a_description_skips_a_letterhead_with_windows_line_endings():
    assert describe.opening_sentence(LETTERHEAD.replace("\n", "\r\n")).startswith("We confirm our respects")


def test_a_publisher_fallback_based_on_windows_line_endings_is_replaced():
    body = "I enclose the new report about the storage batteries and all of our recent tests."
    text = ("[Letterhead] The excellent automatic supply company\r\nThirty seven Grafton Street, New York"
            f"\r\n\r\nDear Sir,\r\n\r\n{body}")
    original = f"The excellent automatic supply company Thirty seven Grafton Street, New York Dear Sir, {body}"
    assert describe.describe(record(text, original)) == body


def test_a_short_body_does_not_make_the_description_fall_back_to_the_address():
    text = "The offices of the Edison Electric Illuminating Company, Thirty Main Street, New York.\n\nDear Sir,\n\nI agree."
    assert describe.opening_sentence(text) == ""


def test_a_description_is_kept_when_the_body_has_no_prose():
    text = "[Letterhead] WESTERN UNION\n\nRECEIVED AT\nNEW YORK"
    assert describe.describe(record(text, "Western Union.")) == "Western Union."


CONDITIONS_FORM = """[Letterhead] THE WESTERN UNION TELEGRAPH COMPANY.

     This Company TRANSMITS and DELIVERS messages only on conditions limiting its liability, which have been assented to by the sender of the following message. Errors can be guarded against only by repeating a message back to the sending station for comparison.

THOS. T. ECKERT, General Manager. NORVIN GREEN, President.

NUMBER SENT BY REC'D BY CHECK
154 Dg G Sdh
Received at No. 16 Broad St. (Next door to Stock Exchange.)
Dated Portland Ogn 5
To H Villard
Mills Bldg NY
Committee decided last night in favor of Edison
C A Prescott
"""


def test_a_telegram_is_described_by_its_message_regression_form_conditions_as_descriptions_22_november():
    telegram = {"transcription": CONDITIONS_FORM, "description": "", "type": "Telegram"}
    assert describe.describe(telegram) == "Committee decided last night in favor of Edison"


def test_telegraph_form_conditions_never_open_a_description():
    assert not describe.opening_sentence(CONDITIONS_FORM).startswith("This Company")


def test_a_telegram_message_inside_the_masthead_block_is_found():
    text = ("[Letterhead] Form No. 260.\nTHE WESTERN UNION TELEGRAPH COMPANY.\n24,000 OFFICES IN AMERICA. CABLE SERVICE TO ALL THE WORLD.\n"
            "Receiver's No. Time Filed Check\nSEND the following message subject to the terms on back hereof, which are hereby agreed to.\n"
            "October 28, 1911.\nMr. Hobart Bosworth,\nLos Angeles, Cal.\n"
            "Shocked and distressed. Hope accounts Selig's condition exaggerated. My best wishes.\nThomas A. Edison.")
    assert describe.telegram_message(text) == "Shocked and distressed. Hope accounts Selig's condition exaggerated."


def test_a_telegram_sent_in_capitals_is_described_by_its_message():
    text = ("[Letterhead] WESTERN UNION TELEGRAM\nNEWCOMB CARLTON, PRESIDENT GEORGE W. E. ATKINS, FIRST VICE-PRESIDENT\nRECEIVED AT\n"
            "214 MAIN STREET\nCOR. ESSEX AVE., ORANGE, N. J.\n174NYO 15\nPORTHURON MICH 1213PM JUNE 3-1920\n"
            "THOS A EDISON CARE H W MEADOWCROFT\nEDISON LAB ORANGE NJ\n"
            "CHARLIE ARRIVED TELEGRAPH COMPANY GOT MY TELEGRAM I SENT MONDAY\nALL WRONG LOVE YOUR NIECE\nNELLIE\n114PM")
    assert describe.telegram_message(text) == "CHARLIE ARRIVED TELEGRAPH COMPANY GOT MY TELEGRAM I SENT MONDAY ALL WRONG LOVE YOUR NIECE"


def test_a_telegram_form_field_line_is_not_taken_for_the_message():
    text = ("[Letterhead] Form No. 2.\nThe Anglo-American Telegraph Company, Limited.\n"
            "PLACE FROM Berlin No. Message 199 I No. of Words 11 Received by J Time Received 12:29 P M\n"
            "To Henry Villard Ny\nAushaendigt Meine edison an\nladung thalmann Compagnie\nRosenthal")
    assert describe.telegram_message(text) == "Aushaendigt Meine edison an ladung thalmann Compagnie"


def test_a_telegram_message_in_short_lines_is_not_cut_after_its_first_line():
    text = "Received at CENTRAL CABLE OFFICE, 16 Broad St., New York.\nTo Villard my\nErfindung scheint um\nbahnbrechend . Elektron"
    assert describe.telegram_message(text) == "Erfindung scheint um bahnbrechend . Elektron"


def test_a_telegram_message_keeps_a_first_line_that_stops_mid_sentence():
    text = ("Mrs. Thos. A. Edison,\nChautauqua, N.Y.\n\nMr. Edison saw\nMr. Mallory today and decided\nthat he will not go West.\n"
            "H.A. Allangarton")
    assert describe.telegram_message(text) == "Mr. Edison saw Mr. Mallory today and decided that he will not go West."


def test_the_forms_to_label_is_dropped_when_it_runs_into_the_message():
    text = ("Send the following Cablegram via Commercial Cables, subject to the Conditions printed on the back hereof, which are agreed to.\n"
            "To Don't agree with your view regarding article 14 but will submit legal question")
    assert describe.telegram_message(text).startswith("Don't agree")


def test_a_president_named_in_a_telegram_message_does_not_hide_it():
    text = "WASHINGTON.\n\n11 November, 1920.\n\nThe President of the United States takes pleasure in presenting the medal to Mr. Edison."
    assert describe.telegram_message(text).startswith("The President")


def test_a_separator_above_a_telegram_message_does_not_break_description_generation():
    message = "Please send the storage battery report back to me tomorrow without fail."
    assert describe.describe({**record(f"---\n{message}", ""), "type": "Telegram"}) == message


def test_care_used_in_a_telegram_message_is_kept():
    message = "The telegraph company asks us to use care when moving the new battery."
    assert describe.telegram_message(f"Received at No. 16 Broad Street\n{message}") == message


def test_a_manager_named_in_a_capitalized_telegram_does_not_hide_the_message():
    message = "PLEASE SEND TWO BATTERIES TO OUR GENERAL MANAGER"
    assert describe.telegram_message(f"April 24, 1920\n{message}") == message


def test_a_letter_that_mentions_telegraph_conditions_keeps_its_opening():
    message = "The cable company sent us a notice on conditions limiting its liability should the shipment be delayed."
    assert describe.describe({**record(message, ""), "type": "Letter"}) == message


def test_a_postal_telegraph_form_notice_is_not_taken_for_the_message():
    text = ("[Letterhead] POSTAL TELEGRAPH-COMMERCIAL CABLES\nThis is a fast Day Telegram unless otherwise indicated by signal after the "
            "number of words: \"N.L.\" (Night Lettergram) or \"Nite\" (Night Telegram).\nDETROIT MICH MAY 29TH, 1920.\n"
            "W.H.MEADOWCROFT,\nCARE THOS A EDISON, ORANGE, NJ.\nKINDLY FORWARD POUND YELLOW URANINE AS PER TELEGRAM EVEN DATE.\nE.G.LIEBOLD.")
    assert describe.telegram_message(text) == "KINDLY FORWARD POUND YELLOW URANINE AS PER TELEGRAM EVEN DATE."
