/**
 * Re-inject a completed questionnaire's saved answers into its own HTML.
 *
 * ITS OWN MODULE, with no imports, so the test loads the REAL function rather than a mirror.
 *
 * P0c1 (PLAN_harness-ux-overhaul §6). TWO injection holes, not one:
 *
 * 1. `</script>` IN A SAVED ANSWER. JSON.stringify escapes for a JSON STRING, not for a SCRIPT
 *    ELEMENT. The HTML parser finds `</script` before any JavaScript runs, so an answer
 *    containing a closing script tag ended this block early and everything after it was parsed
 *    as MARKUP on the dashboard's own origin. Stored, not reflected: the payload arrives as an
 *    answer and fires for every later viewer. Fixed live as P0c1; this is the public port.
 *    `<` is the only character that can begin a closing tag; `>` and `&` are escaped so the
 *    output cannot end a comment or an entity if this block is ever edited into another
 *    context; U+2028/U+2029 are legal in JSON but are LINE TERMINATORS in JavaScript source.
 *    These are \\uXXXX escapes INSIDE a string literal, so the parsed value is byte-identical:
 *    the data round-trips and only the source text changes. Entity-encoding (&lt;) would be
 *    wrong — this is a JavaScript context, and `&lt;` would arrive in the DOM literally.
 *
 * 2. `$` PATTERNS IN THE REPLACEMENT — found while porting, and present in BOTH trees.
 *    `html.replace('</body>', inject + '</body>')` passes a STRING replacement, and JavaScript
 *    expands `$&`, `$\``, `$'` and `$$` inside it. An answer containing `$\`` splices the whole
 *    document BEFORE `</body>` — raw `<`, raw quotes, the page's own `</script>` tags — into the
 *    middle of the escaped JSON literal, undoing hole 1's fix from the other side. A FUNCTION
 *    replacer is never pattern-expanded, so the injection is inserted verbatim.
 */

/** JSON that is safe to place inside a <script> element. */
export function scriptSafeJson(value: unknown): string {
  return JSON.stringify(value)
    .replace(/</g, '\\u003c')
    .replace(/>/g, '\\u003e')
    .replace(/&/g, '\\u0026')
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029');
}

/** The restore script for a set of saved answers. */
export function answerRestoreScript(answers: unknown): string {
  return `<script>
(function() {
  const saved = ${scriptSafeJson(answers)};
  Object.entries(saved).forEach(function(entry) {
    const qid = entry[0];
    const data = entry[1];
    if (data.value) {
      const radio = document.querySelector('input[name="' + qid + '"][value="' + data.value + '"]');
      if (radio) { radio.checked = true; radio.dispatchEvent(new Event('change', {bubbles:true})); }
    }
    if (data.notes) {
      const ta = document.querySelector('textarea[data-other="' + qid + '"]');
      if (ta) ta.value = data.notes;
    }
  });
  if (typeof updateProgress === 'function') updateProgress();
})();
</script>`;
}

/** The questionnaire HTML with the restore script placed before </body>. Unchanged if none. */
export function injectSavedAnswers(html: string, answers: unknown): string {
  const inject = answerRestoreScript(answers);
  // A FUNCTION replacer: its return value is inserted verbatim, never `$`-expanded. See hole 2.
  return html.replace('</body>', () => inject + '</body>');
}
