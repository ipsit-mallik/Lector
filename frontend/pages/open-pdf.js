// Opening a PDF, the same way from every screen (hardening H5): Home (a
// Recent card, the Open dialog, a drop, voice), Reading and Settings (a drop).
//
// `Api.open_pdf` answers a PDF it can't open with a sentence and a reason
// (src/lector/features/reading/open_errors.py) and leaves whatever was open
// as it was, so a failure shows that sentence and the screen stays put. A
// password-protected PDF asks for its password (password-dialog.js) and is
// tried again with it until it opens or the reader cancels.

const OPEN_FAILED = "Couldn't open the PDF. It may be damaged or no longer there.";

function pdfFileName(path) {
  return path.split(/[\\/]/).pop();
}

// The bridge call, with a rejected one (Lector's own error, not the PDF's)
// turned into the generic sentence. Its text goes to the console only.
async function callOpenPdf(method, ...args) {
  try {
    return await callApi(method, ...args);
  } catch (err) {
    console.error("open_pdf failed:", err);
    return { error: OPEN_FAILED, reason: "unknown" };
  }
}

function askForPassword(method, path) {
  return PasswordDialog.ask({
    name: pdfFileName(path),
    attempt: async (password) => {
      const result = await callOpenPdf(method, path, password);
      if (result && result.reason === "wrong_password") return { error: result.error };
      return { done: result };
    },
  });
}

// Resolves to what Python says of the opened document (its state, `page_count`
// and all; always truthy) once `path` is the open document; false when it
// couldn't be opened (the reason has been shown) or the password box was
// cancelled.
// `onMissing(message)`, if given, shows a missing file's message instead of
// the plain toast (Home offers to remove it from Recent).
// `forOrganise` opens it only to organise its pages (Home's Tools page, a
// Recent or Favorites menu): Recent and its reading position are left alone
// until Organise's Done, and its Cancel closes it again
// (Api.open_pdf_to_organise).
async function openPdf(path, { onMissing, forOrganise = false } = {}) {
  const method = forOrganise ? "open_pdf_to_organise" : "open_pdf";
  let result = await callOpenPdf(method, path);
  if (result && result.reason === "password") {
    result = await askForPassword(method, path);
    if (!result) return false;
  }
  if (result && result.error) {
    if (result.reason === "missing" && onMissing) onMissing(result.error);
    else showToast(result.error);
    return false;
  }
  return result || true;
}
