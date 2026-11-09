// Maliit lang na JavaScript: confirm dialog, end-time preview, at "Already booked" panel.
// Server pa rin ang may huling check; pang-ginhawa lang ng user ito.
(function () {
  "use strict";

  // Keep the selected color scheme across pages and visits.
  var themeToggle = document.getElementById("theme-toggle");
  function setTheme(theme, persist) {
    document.documentElement.dataset.theme = theme;
    if (!themeToggle) { return; }
    var dark = theme === "dark";
    themeToggle.setAttribute("aria-label", "Switch to " + (dark ? "light" : "dark") + " theme");
    themeToggle.querySelector(".theme-toggle-label").textContent = dark ? "Light theme" : "Dark theme";
    themeToggle.querySelector(".theme-toggle-icon").textContent = dark ? "☼" : "◐";
    if (persist) {
      try { localStorage.setItem("clinic-theme", theme); } catch (_) { /* Theme still works for this page. */ }
    }
  }
  if (themeToggle) {
    themeToggle.addEventListener("click", function () {
      setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
    });
    setTheme(document.documentElement.dataset.theme || "light", false);
  }

  // Progressive enhancement: links are visible by default. JS turns them into
  // a collapsible panel only at narrow widths, leaving navigation usable if
  // scripts fail to load.
  var header = document.querySelector(".topbar");
  var nav = document.getElementById("primary-nav");
  var navToggle = document.getElementById("nav-toggle");
  if (header && nav && navToggle) {
    var mobileNav = window.matchMedia("(max-width: 800px)");
    var navOpen = false;

    function updateNavMode() {
      var isMobile = mobileNav.matches;
      if (!isMobile) { navOpen = false; }
      navToggle.hidden = !isMobile;
      nav.hidden = isMobile && !navOpen;
      navToggle.setAttribute("aria-expanded", String(isMobile && navOpen));
      navToggle.setAttribute("aria-label", navOpen ? "Close navigation" : "Open navigation");
      var label = navToggle.querySelector(".sr-only");
      if (label) { label.textContent = navOpen ? "Close navigation" : "Open navigation"; }
      document.documentElement.classList.toggle("nav-js", true);
    }

    navToggle.addEventListener("click", function () {
      if (!mobileNav.matches) { return; }
      navOpen = !navOpen;
      updateNavMode();
    });
    nav.addEventListener("click", function (event) {
      if (event.target.closest("a") && mobileNav.matches && navOpen) {
        navOpen = false;
        updateNavMode();
      }
    });
    document.addEventListener("click", function (event) {
      if (navOpen && !nav.contains(event.target) && !navToggle.contains(event.target)) {
        navOpen = false;
        updateNavMode();
      }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && navOpen) {
        navOpen = false;
        updateNavMode();
        navToggle.focus();
      }
    });
    if (mobileNav.addEventListener) {
      mobileNav.addEventListener("change", updateNavMode);
    } else if (mobileNav.addListener) {
      mobileNav.addListener(updateNavMode);
    }
    updateNavMode();
  }

  // Honor confirmation prompts already declared on destructive forms and
  // prevent a second accidental submission while the first request is sent.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) { return; }
    var message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
      return;
    }
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = "true";
    form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (button) {
      button.disabled = true;
    });
  });

  // flash messages: manual close button, and clean up the element once its
  // auto-fade animation finishes so it's not left sitting invisibly on top
  document.querySelectorAll(".flash").forEach(function (flash) {
    var close = flash.querySelector(".flash-close");
    if (close) {
      close.addEventListener("click", function () { flash.remove(); });
    }
    flash.addEventListener("animationend", function (event) {
      if (event.animationName === "flash-out") { flash.remove(); }
    });
  });

  // "More" action menus (<details class="menu">): isa lang ang bukas nang sabay,
  // at magsasara kapag nag-click sa labas nito
  var openMenus = document.querySelectorAll("details.menu");
  openMenus.forEach(function (menu) {
    menu.addEventListener("toggle", function () {
      if (menu.open) {
        openMenus.forEach(function (other) {
          if (other !== menu) { other.open = false; }
        });
      }
    });
  });
  document.addEventListener("click", function (event) {
    openMenus.forEach(function (menu) {
      if (menu.open && !menu.contains(event.target)) { menu.open = false; }
    });
  });

  function to12h(hhmm) {
    var parts = hhmm.split(":");
    var hours = parseInt(parts[0], 10);
    var suffix = hours >= 12 ? "PM" : "AM";
    return (hours % 12 || 12) + ":" + parts[1] + " " + suffix;
  }

  function addMinutes(hhmm, minutes) {
    var parts = hhmm.split(":");
    var total = parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10) + minutes;
    var h = String(Math.floor(total / 60)).padStart(2, "0");
    var m = String(total % 60).padStart(2, "0");
    return h + ":" + m;
  }

  // ipakita kung anong oras matatapos base sa service at start time
  var serviceSelect = document.getElementById("service_id");
  var timeSelect = document.getElementById("start_time");
  var endPreview = document.getElementById("end-preview");
  var duration = null;

  function updateEnd() {
    if (!endPreview || !timeSelect) { return; }
    if (serviceSelect) {
      var option = serviceSelect.options[serviceSelect.selectedIndex];
      duration = option && option.dataset.duration ? parseInt(option.dataset.duration, 10) : null;
    } else {
      duration = parseInt(endPreview.dataset.duration, 10);
    }
    if (duration && timeSelect.value) {
      endPreview.textContent = "Ends at " + to12h(addMinutes(timeSelect.value, duration)) +
        " (" + duration + " minutes)";
    } else {
      endPreview.textContent = "";
    }
  }

  if (timeSelect && endPreview) {
    timeSelect.addEventListener("change", updateEnd);
    if (serviceSelect) { serviceSelect.addEventListener("change", updateEnd); }
    updateEnd();
  }

  // "Already booked" panel: kumukuha ng data sa /api/booked-slots tuwing nagbabago ang staff o date
  var panel = document.getElementById("slots-panel");
  var resourceSelect = document.getElementById("resource_id");
  var dateInput = document.getElementById("appt_date");
  var slotsHeading = document.getElementById("slots-heading");
  var slotsNote = document.getElementById("slots-note");
  var slotsList = document.getElementById("slots-list");
  var slotsRequest = 0;
  var validStatuses = ["Pending", "Confirmed", "Completed", "Cancelled"];

  function renderSlots(data) {
    if (!slotsHeading || !slotsNote || !slotsList || !data) { return; }
    slotsHeading.textContent = data.heading;
    slotsNote.textContent = data.note;

    slotsList.textContent = "";
    data.slots.forEach(function (slot) {
      var item = document.createElement("li");

      var time = document.createElement("span");
      time.className = "slot-time";
      time.textContent = slot.start + " to " + slot.end;

      var meta = document.createElement("span");
      meta.className = "slot-meta";
      meta.textContent = slot.service + " ";

      // textContent lang ang gamit (hindi innerHTML) para hindi ma-inject ng kung ano ang laman ng pangalan
      var badge = document.createElement("span");
      badge.className = "badge badge-" + slot.status.toLowerCase();
      badge.textContent = slot.status;
      meta.appendChild(badge);

      item.appendChild(time);
      item.appendChild(meta);
      slotsList.appendChild(item);
    });
  }

  function loadSlots() {
    if (!panel || !resourceSelect || !dateInput || !slotsHeading || !slotsNote || !slotsList) { return; }
    var requestNumber = ++slotsRequest;
    if (!resourceSelect.value || !dateInput.value) {
      slotsHeading.textContent = "Choose a staff member and date";
      slotsList.textContent = "";
      slotsNote.textContent = "Select both fields to see booked times.";
      return;
    }

    slotsNote.textContent = "Loading booked times…";
    slotsList.textContent = "";
    var url = panel.dataset.api +
      "?resource_id=" + encodeURIComponent(resourceSelect.value) +
      "&date=" + encodeURIComponent(dateInput.value);
    if (panel.dataset.exclude) {
      url += "&exclude=" + encodeURIComponent(panel.dataset.exclude);
    }
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) { throw new Error("Request failed"); }
        return response.json();
      })
      .then(function (data) {
        if (requestNumber !== slotsRequest) { return; }
        if (!data || typeof data.heading !== "string" || typeof data.note !== "string" ||
            !Array.isArray(data.slots) || !data.slots.every(function (slot) {
              return slot && typeof slot.start === "string" && typeof slot.end === "string" &&
                typeof slot.service === "string" && validStatuses.indexOf(slot.status) !== -1;
            })) {
          throw new Error("Unexpected booked-times response");
        }
        renderSlots(data);
      })
      .catch(function () {
        if (requestNumber !== slotsRequest || !slotsNote) { return; }
        slotsNote.textContent = "Could not load booked times. Check your connection and try again.";
      });
  }

  if (panel && resourceSelect && dateInput && slotsHeading && slotsNote && slotsList) {
    resourceSelect.addEventListener("change", loadSlots);
    dateInput.addEventListener("change", loadSlots);
  }
})();
