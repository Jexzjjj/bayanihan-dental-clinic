// Maliit lang na JavaScript: confirm dialog, end-time preview, at "Already booked" panel.
// Server pa rin ang may huling check; pang-ginhawa lang ng user ito.
(function () {
  "use strict";

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

  function renderSlots(data) {
    document.getElementById("slots-heading").textContent = data.heading;
    document.getElementById("slots-note").textContent = data.note;

    var list = document.getElementById("slots-list");
    list.textContent = "";
    data.slots.forEach(function (slot) {
      var item = document.createElement("li");

      var time = document.createElement("span");
      time.className = "slot-time";
      time.textContent = slot.start + " to " + slot.end;

      var meta = document.createElement("span");
      meta.className = "slot-meta";
      meta.textContent = slot.client + " / " + slot.service + " ";

      // textContent lang ang gamit (hindi innerHTML) para hindi ma-inject ng kung ano ang laman ng pangalan
      var badge = document.createElement("span");
      badge.className = "badge badge-" + slot.status.toLowerCase();
      badge.textContent = slot.status;
      meta.appendChild(badge);

      item.appendChild(time);
      item.appendChild(meta);
      list.appendChild(item);
    });
  }

  function loadSlots() {
    var url = panel.dataset.api +
      "?resource_id=" + encodeURIComponent(resourceSelect.value) +
      "&date=" + encodeURIComponent(dateInput.value);
    if (panel.dataset.exclude) {
      url += "&exclude=" + encodeURIComponent(panel.dataset.exclude);
    }
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (response) { return response.json(); })
      .then(renderSlots)
      .catch(function () {
        document.getElementById("slots-note").textContent = "Could not load the booked slots. Please try again.";
      });
  }

  if (panel && resourceSelect && dateInput) {
    resourceSelect.addEventListener("change", loadSlots);
    dateInput.addEventListener("change", loadSlots);
  }
})();
