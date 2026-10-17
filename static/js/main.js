/* Small helper scripts for EditMatch.
   The server does all the real work. This file only:
   - stops double submissions,
   - shows the names of chosen files,
   - fills the project description when an example is clicked.
*/

(function () {
  "use strict";

  // 1. Stop a form from being sent twice (for example, after a double click).
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (form.dataset.submitting === "1") {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = "1";
      var button = event.submitter;
      if (button) {
        var loadingText = button.dataset.loadingText;
        // Wait one tick so the browser still sends the button's own name and value.
        setTimeout(function () {
          button.disabled = true;
          if (loadingText) button.textContent = loadingText;
        }, 0);
      }
    });
  });

  // 2. Show the names of files the user picked, under each file input.
  document.querySelectorAll("input[type='file']").forEach(function (input) {
    var output = document.getElementById(input.id + "-names");
    if (!output) return;
    input.addEventListener("change", function () {
      var names = Array.prototype.map.call(input.files, function (file) {
        return file.name;
      });
      output.textContent = names.length ? "Selected: " + names.join(", ") : "";
    });
  });

  // 3. Clicking an example prompt fills the description box. The user can still edit it.
  document.querySelectorAll("[data-fill-target]").forEach(function (chip) {
    chip.addEventListener("click", function () {
      var target = document.getElementById(chip.dataset.fillTarget);
      if (!target) return;
      target.value = chip.dataset.fillText || chip.textContent.trim();
      target.focus();
    });
  });
})();
