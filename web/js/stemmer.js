// The Porter stemmer (1980), the twin of builder/stemmer.py; tests/fixtures/stems.json holds them to the same answers.
const VOWELS = new Set(["a", "e", "i", "o", "u"]);

function consonant(word, i) {
  if (VOWELS.has(word[i])) return false;
  if (word[i] === "y") return i === 0 || !consonant(word, i - 1);
  return true;
}

function measure(stem) {
  let count = 0;
  let i = 0;
  const n = stem.length;
  while (i < n && consonant(stem, i)) i++;
  while (i < n) {
    while (i < n && !consonant(stem, i)) i++;
    if (i >= n) break;
    count++;
    while (i < n && consonant(stem, i)) i++;
  }
  return count;
}

function hasVowel(stem) {
  for (let i = 0; i < stem.length; i++) if (!consonant(stem, i)) return true;
  return false;
}

function doubleConsonant(word) {
  return word.length >= 2 && word.at(-1) === word.at(-2) && consonant(word, word.length - 1);
}

function cvc(word) {
  const n = word.length;
  return n >= 3 && consonant(word, n - 1) && !consonant(word, n - 2) && consonant(word, n - 3) && !"wxy".includes(word[n - 1]);
}

function replace(word, rules, minimum) {
  for (const [suffix, replacement] of rules) {
    if (word.endsWith(suffix)) {
      const stem = word.slice(0, word.length - suffix.length);
      return measure(stem) > minimum ? stem + replacement : word;
    }
  }
  return word;
}

const byLength = (a, b) => b[0].length - a[0].length;
const STEP2 = [["ational", "ate"], ["tional", "tion"], ["enci", "ence"], ["anci", "ance"], ["izer", "ize"], ["abli", "able"],
  ["alli", "al"], ["entli", "ent"], ["eli", "e"], ["ousli", "ous"], ["ization", "ize"], ["ation", "ate"], ["ator", "ate"],
  ["alism", "al"], ["iveness", "ive"], ["fulness", "ful"], ["ousness", "ous"], ["aliti", "al"], ["iviti", "ive"],
  ["biliti", "ble"]].sort(byLength);
const STEP3 = [["icate", "ic"], ["ative", ""], ["alize", "al"], ["iciti", "ic"], ["ical", "ic"], ["ful", ""], ["ness", ""]].sort(byLength);
const STEP4 = ["al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement", "ment", "ent", "ion", "ou", "ism", "ate", "iti",
  "ous", "ive", "ize"].sort((a, b) => b.length - a.length);

/** A word's stem; numbers, identifiers and words of two letters or fewer are left as they are. */
export function stem(input) {
  let word = input;
  if (word.length <= 2 || !/^[a-z]+$/.test(word)) return word;
  if (word.endsWith("sses")) word = word.slice(0, -2);
  else if (word.endsWith("ies")) word = word.slice(0, -2);
  else if (word.endsWith("ss")) { /* kept */ } else if (word.endsWith("s")) word = word.slice(0, -1);
  if (word.endsWith("eed")) {
    if (measure(word.slice(0, -3)) > 0) word = word.slice(0, -1);
  } else {
    let trimmed = null;
    for (const suffix of ["ed", "ing"]) {
      if (word.endsWith(suffix) && hasVowel(word.slice(0, -suffix.length))) { trimmed = word.slice(0, -suffix.length); break; }
    }
    if (trimmed !== null) {
      word = trimmed;
      if (word.endsWith("at") || word.endsWith("bl") || word.endsWith("iz")) word += "e";
      else if (doubleConsonant(word) && !"lsz".includes(word.at(-1))) word = word.slice(0, -1);
      else if (measure(word) === 1 && cvc(word)) word += "e";
    }
  }
  if (word.endsWith("y") && hasVowel(word.slice(0, -1))) word = `${word.slice(0, -1)}i`;
  word = replace(word, STEP2, 0);
  word = replace(word, STEP3, 0);
  for (const suffix of STEP4) {
    if (word.endsWith(suffix)) {
      const part = word.slice(0, word.length - suffix.length);
      if (measure(part) > 1 && (suffix !== "ion" || part.endsWith("s") || part.endsWith("t"))) word = part;
      break;
    }
  }
  if (word.endsWith("e")) {
    const part = word.slice(0, -1);
    if (measure(part) > 1 || (measure(part) === 1 && !cvc(part))) word = part;
  }
  if (measure(word) > 1 && doubleConsonant(word) && word.endsWith("l")) word = word.slice(0, -1);
  return word;
}
