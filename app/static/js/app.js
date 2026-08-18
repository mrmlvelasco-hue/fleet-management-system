/* Shell behaviour: dark-mode + sidebar-collapse persisted via cookie,
   DataTables/Select2 auto-init. */
(function () {
  function setCookie(name, value) {
    document.cookie = name + "=" + value + ";path=/;max-age=31536000;SameSite=Lax";
  }
  function getCookie(name) {
    const m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return m ? m.pop() : "";
  }

  // Dark mode
  const savedTheme = getCookie("fms-theme");
  if (savedTheme) document.documentElement.setAttribute("data-bs-theme", savedTheme);
  const darkToggle = document.getElementById("darkModeToggle");
  if (darkToggle) {
    darkToggle.addEventListener("click", function () {
      const html = document.documentElement;
      const next = html.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
      html.setAttribute("data-bs-theme", next);
      setCookie("fms-theme", next);
    });
  }

  // Sidebar collapse / mobile reveal.
  //
  // The CSS (theme.css) already had two rules for this -- `.fms-app.
  // is-collapsed` narrows the sidebar to an icon rail on desktop, and
  // `.fms-app.is-open .fms-sidebar` slides the sidebar on-screen on
  // mobile (where it is off-canvas by default). Neither ever fired: the
  // wrapper element was missing the `fms-app` class the rules are
  // scoped to, and this toggle was setting a THIRD, unrelated class
  // name (`sidebar-collapsed`) that no CSS rule reads at all. On a
  // phone that left the hamburger button doing nothing -- the sidebar
  // was permanently off-screen with no way to bring it back.
  const wrapper = document.getElementById("fms-wrapper");
  const isMobile = () => window.matchMedia("(max-width: 860px)").matches;

  if (wrapper && !isMobile() && getCookie("fms-sidebar") === "collapsed") {
    wrapper.classList.add("is-collapsed");
  }
  const sbToggle = document.getElementById("sidebarToggle");
  if (sbToggle && wrapper) {
    sbToggle.addEventListener("click", function () {
      if (isMobile()) {
        // Mobile: reveal/hide the off-canvas sidebar. Not persisted --
        // a phone should always start with it closed, not resume
        // whatever state it was left in on a totally different device.
        wrapper.classList.toggle("is-open");
      } else {
        // Desktop: collapse to an icon-only rail, persisted across
        // visits like before.
        wrapper.classList.toggle("is-collapsed");
        setCookie("fms-sidebar",
          wrapper.classList.contains("is-collapsed") ? "collapsed" : "open");
      }
    });
  }
  // Tapping the darkened backdrop, or any navigation link, closes the
  // mobile sidebar -- otherwise it stays covering the content after the
  // person has already chosen where they're going.
  if (wrapper) {
    document.addEventListener("click", function (e) {
      if (!isMobile() || !wrapper.classList.contains("is-open")) return;
      const insideSidebar = e.target.closest(".fms-sidebar");
      const isToggleButton = e.target.closest("#sidebarToggle");
      if (!insideSidebar || (insideSidebar && e.target.closest("a"))) {
        if (!isToggleButton) wrapper.classList.remove("is-open");
      }
    });
  }
  // A resize from mobile to desktop (e.g. rotating a tablet, or a
  // devtools resize) must not leave a stale is-open state active once
  // the mobile-only CSS that gives it meaning no longer applies.
  window.addEventListener("resize", function () {
    if (wrapper && !isMobile()) wrapper.classList.remove("is-open");
  });

  // Sidebar category groups: the server renders the group containing the
  // current page as expanded and the others collapsed by default: this
  // cookie layer only overrides that default once a person has manually
  // toggled a group, so their preference sticks across page loads within
  // the same section, without fighting the auto-expand-active-group logic.
  document.querySelectorAll(".sidebar-group-toggle").forEach(function (btn) {
    var targetId = btn.getAttribute("data-bs-target");
    var target = document.querySelector(targetId);
    if (!target) return;
    var stateKey = "fms-sbgroup-" + targetId.replace("#", "");
    var saved = getCookie(stateKey);
    if (saved === "collapsed") {
      target.classList.remove("show");
    } else if (saved === "expanded") {
      target.classList.add("show");
    }
    // Reflect whatever the actual resulting state is, rather than
    // assuming — this keeps the chevron direction and screen-reader
    // state correct regardless of whether the server default or a
    // cookie override won.
    btn.setAttribute("aria-expanded", target.classList.contains("show") ? "true" : "false");

    target.addEventListener("shown.bs.collapse", function () {
      setCookie(stateKey, "expanded");
      btn.setAttribute("aria-expanded", "true");
    });
    target.addEventListener("hidden.bs.collapse", function () {
      setCookie(stateKey, "collapsed");
      btn.setAttribute("aria-expanded", "false");
    });
  });

  // Sidebar appearance picker.
  //
  // The skin is already correct on first paint -- the server renders
  // data-sidebar-skin into <html> -- so this only handles CHANGING it.
  // The attribute is swapped immediately and the save happens in the
  // background, because waiting for a round-trip before showing the
  // new colour makes a purely visual choice feel sluggish.
  //
  // If the save fails the attribute is put back, rather than leaving
  // someone looking at a skin that won't survive their next page load.
  document.querySelectorAll("[data-skin-code]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var code = btn.getAttribute("data-skin-code");
      var root = document.documentElement;
      var previous = root.getAttribute("data-sidebar-skin");
      if (code === previous) return;

      root.setAttribute("data-sidebar-skin", code);
      // Every swatch in BOTH pickers (desktop dropdown and mobile
      // sheet) is updated, not just the one clicked -- they render the
      // same choice and must not disagree.
      document.querySelectorAll("[data-skin-code]").forEach(function (other) {
        var on = other.getAttribute("data-skin-code") === code;
        other.classList.toggle("is-selected", on);
        other.setAttribute("aria-pressed", on ? "true" : "false");
      });

      var token = document.querySelector('meta[name="csrf-token"]');
      fetch("/preferences/sidebar-skin", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": token ? token.getAttribute("content") : ""
        },
        body: JSON.stringify({ skin: code })
      }).then(function (r) {
        if (!r.ok) throw new Error("save failed");
      }).catch(function () {
        root.setAttribute("data-sidebar-skin", previous);
        document.querySelectorAll("[data-skin-code]").forEach(function (o) {
          var on = o.getAttribute("data-skin-code") === previous;
          o.classList.toggle("is-selected", on);
          o.setAttribute("aria-pressed", on ? "true" : "false");
        });
        if (window.Swal) {
          Swal.fire({ icon: "error", title: "Couldn't save that",
                      text: "Your sidebar appearance was not changed.",
                      timer: 2600, showConfirmButton: false });
        }
      });
    });
  });

  // Row action menus inside scrollable tables.
  //
  // `.table-responsive` puts overflow on the wrapper so a wide table
  // scrolls sideways instead of widening the page. An overflow container
  // clips its children, though, so the three-dot row menu gets cut off
  // at the table's edge -- and on a list with one or two rows the
  // wrapper is barely taller than a row, so everything below the first
  // menu item disappears. That is why a single-result list appeared to
  // offer only "View Details": Edit was rendered, just clipped.
  //
  // Lifting the overflow only WHILE a menu is open keeps horizontal
  // scrolling intact the rest of the time. Bootstrap fires these events
  // on the toggle, and they bubble, so one pair of listeners on the
  // document covers every table in the app -- including any added later.
  document.addEventListener("show.bs.dropdown", function (e) {
    var wrap = e.target.closest(".table-responsive");
    if (wrap) wrap.classList.add("has-open-dropdown");
  });
  document.addEventListener("hide.bs.dropdown", function (e) {
    var wrap = e.target.closest(".table-responsive");
    if (wrap) wrap.classList.remove("has-open-dropdown");
  });

  // "Extract" on a scanned CR/OR: read it, then let the person choose.
  //
  // Nothing is written until they tick a field and press Apply, and a
  // field the system could not verify cannot be applied without an
  // explicit confirmation -- the value is offered, never assumed. A
  // half-right chassis number silently filled into a form is exactly
  // the failure this flow exists to prevent.
  document.querySelectorAll(".extract-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var attId = btn.dataset.attachmentId;
      var vehicleId = btn.dataset.vehicleId;
      var original = btn.innerHTML;
      // OCR takes a few seconds; say so rather than look frozen.
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Reading…';

      fetch("/master/attachments/" + attId + "/extract", {
        method: "POST",
        headers: {"X-CSRFToken": (document.querySelector('meta[name="csrf-token"]') || {}).content || ""}
      }).then(function (r) { return r.json(); })
        .then(function (data) { showExtractionReview(data, vehicleId); })
        .catch(function () {
          if (window.Swal) Swal.fire({icon: "error",
            title: "Could not read that document",
            text: "Please enter the details manually."});
        })
        .finally(function () { btn.disabled = false; btn.innerHTML = original; });
    });
  });

  window.showExtractionReview = function (data, vehicleId) {
    var trusted = data.trusted || {}, unverified = data.unverified || {};
    if (!Object.keys(trusted).length && !Object.keys(unverified).length) {
      if (window.Swal) Swal.fire({icon: "info", title: "Nothing readable",
                                  text: data.message});
      return;
    }
    function rows(group, verified) {
      return Object.keys(group).map(function (name) {
        var f = group[name];
        var label = name.replace(/_/g, " ");
        return '<tr>' +
          '<td><input type="checkbox" class="form-check-input xf-pick" ' +
              'data-field="' + name + '" ' + (verified ? "checked" : "") + '></td>' +
          '<td class="text-capitalize small">' + label + '</td>' +
          '<td class="font-monospace small">' + (f.value || "") + '</td>' +
          '<td><span class="badge text-bg-' +
              (verified ? "success" : "warning") + '">' + f.confidence + '</span></td>' +
          '<td class="small text-muted">' + (f.note || "") +
            (verified ? "" :
              '<div class="form-check mt-1"><input class="form-check-input xf-confirm" ' +
              'type="checkbox" data-field="' + name + '" id="cf-' + name + '">' +
              '<label class="form-check-label small" for="cf-' + name + '">' +
              "I've checked this against the document</label></div>") +
          '</td></tr>';
      }).join("");
    }
    var html =
      '<p class="small text-muted">' + (data.message || "") + '</p>' +
      '<div class="table-responsive"><table class="table table-sm align-middle">' +
      '<thead><tr><th></th><th>Field</th><th>Read as</th><th>Confidence</th>' +
      '<th>Notes</th></tr></thead><tbody>' +
      rows(trusted, true) + rows(unverified, false) +
      '</tbody></table></div>';

    if (!window.Swal) { return; }
    Swal.fire({
      title: "Details read from the scan", html: html, width: "56rem",
      showCancelButton: true, confirmButtonText: "Apply selected",
      cancelButtonText: "Cancel",
      preConfirm: function () {
        var all = Object.assign({}, trusted, unverified);
        var selected = [], confirmed = [];
        document.querySelectorAll(".xf-pick:checked").forEach(function (c) {
          selected.push(c.dataset.field); });
        document.querySelectorAll(".xf-confirm:checked").forEach(function (c) {
          confirmed.push(c.dataset.field); });
        var unchecked = selected.filter(function (n) {
          return all[n] && all[n].needs_review && confirmed.indexOf(n) === -1; });
        if (unchecked.length) {
          Swal.showValidationMessage(
            "Please confirm you have checked: " + unchecked.join(", "));
          return false;
        }
        return fetch("/master/vehicles/" + vehicleId + "/apply-extraction", {
          method: "POST",
          headers: {"Content-Type": "application/json",
                    "X-CSRFToken": (document.querySelector('meta[name="csrf-token"]') || {}).content || ""},
          body: JSON.stringify({fields: all, selected: selected,
                                confirmed: confirmed})
        }).then(function (r) { return r.json(); });
      }
    }).then(function (res) {
      if (res.isConfirmed && res.value && res.value.ok) {
        Swal.fire({icon: "success", title: "Applied",
                   text: Object.keys(res.value.applied || {}).length +
                         " field(s) updated. Review them before saving."})
            .then(function () { window.location.reload(); });
      }
    });
  };

  // Auto-init DataTables and Select2 when jQuery is present
  if (window.jQuery) {
    jQuery(function ($) {
      $("table.fms-datatable").DataTable({ pageLength: 25 });
      $("select.fms-select2").select2({ width: "100%", theme: "default" });
    });
  }

  // Smart Selector: wire a <select> to a paginated AJAX search endpoint
  // (Select2 remote-data mode). Used for Vehicles/Drivers/Users/Vendors and
  // any future module registered under /api/search/<module>.
  window.initAjaxSelect = function (selector, endpoint, opts) {
    if (!window.jQuery) return;
    opts = opts || {};
    jQuery(selector).select2({
      width: "100%",
      theme: "default",
      placeholder: opts.placeholder || "Type to search...",
      minimumInputLength: 0,
      allowClear: !!opts.allowClear,
      ajax: {
        url: endpoint,
        dataType: "json",
        delay: 300,
        data: function (params) {
          return {
            q: params.term || "",
            page: params.page || 1,
            per_page: opts.perPage || 20
          };
        },
        processResults: function (data, params) {
          params.page = params.page || 1;
          return { results: data.results, pagination: data.pagination };
        },
        cache: true
      }
    });
  };

  // Search Modal: for large datasets (>100 records per the UX threshold
  // rule), a full filter + sort + paginate + Select dialog rather than a
  // dropdown. Reuses the same /api/search/<module>/table endpoints as the
  // AJAX selects (server-side search/sort/filter/pagination).
  //
  // config = {
  //   title, endpoint,
  //   columns: [{key, label, sortable}],
  //   filters: [{key, label, options: [{value, label}]}],
  //   onSelect: function(row) {...}
  // }
  window.openSearchModal = function (config) {
    var modalEl = document.getElementById("fmsSearchModal");
    if (!modalEl || !window.jQuery || !window.bootstrap) return;
    var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    var state = { q: "", page: 1, sortBy: null, sortDir: "asc", filters: {} };

    document.getElementById("fmsSearchModalTitle").textContent = config.title || "Search";

    var head = document.getElementById("fmsSearchModalHead");
    head.innerHTML = "<tr>" + config.columns.map(function (col) {
      var sortAttr = col.sortable ? ' data-sort="' + col.key + '" style="cursor:pointer"' : "";
      return "<th" + sortAttr + ">" + col.label +
        (col.sortable ? ' <i class="bi bi-arrow-down-up small text-muted"></i>' : "") +
        "</th>";
    }).join("") + "<th></th></tr>";

    var filtersBox = document.getElementById("fmsSearchModalFilters");
    filtersBox.innerHTML = (config.filters || []).map(function (f) {
      var opts = '<option value="">' + f.label + ': All</option>' +
        f.options.map(function (o) { return '<option value="' + o.value + '">' + o.label + "</option>"; }).join("");
      return '<select class="form-select form-select-sm fms-modal-filter" data-key="' + f.key + '">' + opts + "</select>";
    }).join("");

    function load() {
      var params = new URLSearchParams();
      params.set("q", state.q);
      params.set("page", state.page);
      params.set("per_page", 10);
      if (state.sortBy) { params.set("sort_by", state.sortBy); params.set("sort_dir", state.sortDir); }
      Object.keys(state.filters).forEach(function (k) {
        if (state.filters[k]) params.set(k, state.filters[k]);
      });
      fetch(config.endpoint + "?" + params.toString())
        .then(function (r) { return r.json(); })
        .then(renderRows)
        .catch(function () {
          document.getElementById("fmsSearchModalBody").innerHTML =
            '<tr><td class="text-center text-danger py-3">Search failed. Please try again.</td></tr>';
        });
    }

    function renderRows(data) {
      var body = document.getElementById("fmsSearchModalBody");
      if (!data.rows || !data.rows.length) {
        body.innerHTML = '<tr><td colspan="' + (config.columns.length + 1) +
          '" class="text-center text-muted py-4">No matching records.</td></tr>';
      } else {
        body.innerHTML = data.rows.map(function (row) {
          return "<tr>" + config.columns.map(function (col) {
            return "<td>" + (row[col.key] === undefined || row[col.key] === null ? "—" : row[col.key]) + "</td>";
          }).join("") +
            '<td><button type="button" class="btn btn-sm btn-primary fms-modal-select">Select</button></td></tr>';
        }).join("");
        Array.prototype.forEach.call(body.querySelectorAll(".fms-modal-select"), function (btn, i) {
          btn.addEventListener("click", function () {
            config.onSelect(data.rows[i]);
            modal.hide();
          });
        });
      }
      document.getElementById("fmsSearchModalSummary").textContent =
        "Showing " + ((data.page - 1) * data.per_page + 1) + "–" +
        Math.min(data.page * data.per_page, data.total) + " of " + data.total;
      renderPagination(data);
    }

    function renderPagination(data) {
      var pager = document.getElementById("fmsSearchModalPagination");
      var pages = [];
      for (var p = 1; p <= data.total_pages; p++) pages.push(p);
      pager.innerHTML = pages.map(function (p) {
        return '<li class="page-item ' + (p === data.page ? "active" : "") + '">' +
          '<a class="page-link" href="#" data-page="' + p + '">' + p + "</a></li>";
      }).join("");
      Array.prototype.forEach.call(pager.querySelectorAll("a[data-page]"), function (a) {
        a.addEventListener("click", function (e) {
          e.preventDefault();
          state.page = parseInt(a.dataset.page, 10);
          load();
        });
      });
    }

    var queryBox = document.getElementById("fmsSearchModalQuery");
    queryBox.value = "";
    var debounceTimer;
    queryBox.oninput = function () {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        state.q = queryBox.value;
        state.page = 1;
        load();
      }, 300);
    };

    Array.prototype.forEach.call(filtersBox.querySelectorAll(".fms-modal-filter"), function (sel) {
      sel.onchange = function () {
        state.filters[sel.dataset.key] = sel.value;
        state.page = 1;
        load();
      };
    });

    head.querySelectorAll("[data-sort]").forEach(function (th) {
      th.onclick = function () {
        var key = th.dataset.sort;
        if (state.sortBy === key) {
          state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
        } else {
          state.sortBy = key;
          state.sortDir = "asc";
        }
        load();
      };
    });

    modal.show();
    load();
  };

  // Convenience wrapper: wires an "Advanced Search" button to open the
  // Vehicle Search Modal and populate a paired Select2 field. Saves every
  // form from repeating the full column/onSelect config.
  window.wireVehicleSearchModal = function (buttonId, selectId, tableEndpoint) {
    var btn = document.getElementById(buttonId);
    if (!btn) return;
    btn.addEventListener("click", function () {
      openSearchModal({
        title: "Search Vehicles",
        endpoint: tableEndpoint,
        columns: [
          { key: "plate", label: "Plate / Conduction No.", sortable: true },
          { key: "brand", label: "Make", sortable: true },
          { key: "model", label: "Model", sortable: true },
          { key: "year", label: "Year", sortable: true },
          { key: "branch", label: "Branch" },
          { key: "status", label: "Status", sortable: true }
        ],
        onSelect: function (row) {
          var $select = jQuery("#" + selectId);
          if ($select.find("option[value='" + row.id + "']").length === 0) {
            $select.append(new Option(row.text, row.id, true, true));
          }
          $select.val(row.id).trigger("change");
        }
      });
    });
  };

  // Notification bell: poll unread count + load recent on open
  function loadNotifications() {
    fetch("/admin/notifications/recent")
      .then(function(r){ return r.json(); })
      .then(function(data) {
        var items = document.getElementById("notifItems");
        if (!items) return;
        if (!data.notifications || data.notifications.length === 0) {
          items.innerHTML = '<li class="px-3 py-2 text-muted small">No notifications</li>';
          return;
        }
        items.innerHTML = data.notifications.map(function(n) {
          return '<li><a class="dropdown-item py-2 ' + (n.is_read ? 'text-muted' : 'fw-semibold') + '" href="#" data-id="' + n.id + '">' +
            '<div class="small">' + n.title + '</div>' +
            '<div class="text-muted" style="font-size:.78rem">' + n.message + '</div></a></li>';
        }).join('');
        items.querySelectorAll("a[data-id]").forEach(function(a) {
          a.addEventListener("click", function(e) {
            e.preventDefault();
            fetch("/admin/notifications/" + a.dataset.id + "/mark-read", {method:"POST",headers:{"X-CSRFToken": getCsrfToken()}});
            a.classList.remove("fw-semibold");
          });
        });
      }).catch(function(){});
  }

  function updateBadge() {
    fetch("/admin/notifications/unread-count")
      .then(function(r){ return r.json(); })
      .then(function(data) {
        var badge = document.getElementById("notifBadge");
        if (!badge) return;
        if (data.count > 0) {
          badge.textContent = data.count > 99 ? "99+" : data.count;
          badge.classList.remove("d-none");
        } else {
          badge.classList.add("d-none");
        }
      }).catch(function(){});
  }

  function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  var bell = document.getElementById("notifBell");
  if (bell) {
    bell.addEventListener("show.bs.dropdown", loadNotifications);
    updateBadge();
    setInterval(updateBadge, 60000);
    var markAll = document.getElementById("markAllRead");
    if (markAll) {
      markAll.addEventListener("click", function(e) {
        e.preventDefault();
        fetch("/admin/notifications/mark-all-read", {method:"POST",headers:{"X-CSRFToken":getCsrfToken()}})
          .then(function(){ updateBadge(); loadNotifications(); });
      });
    }
  }

  // Attachment panels: AJAX upload + delete, no page reload/JSON dump
  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function renderAttachmentRow(att) {
    var thumb = att.is_image
      ? '<a href="' + att.view_url + '" target="_blank"><img src="' + att.view_url +
        '" alt="" style="height:32px;width:32px;object-fit:cover;border-radius:4px;" class="me-2"></a>'
      : '<i class="bi bi-file-earmark me-2"></i>';
    var viewBtn = att.is_image
      ? '<a class="btn btn-sm btn-outline-secondary" target="_blank" href="' + att.view_url + '"><i class="bi bi-eye"></i></a> '
      : '';
    return '<li class="list-group-item d-flex justify-content-between align-items-center" data-attachment-id="' + att.id + '">' +
      '<span>' + thumb + escapeHtml(att.filename) +
      '<span class="text-muted small ms-2">' + (att.size / 1024).toFixed(1) + ' KB</span></span>' +
      '<div>' + viewBtn +
      '<a class="btn btn-sm btn-outline-secondary" href="' + att.download_url + '"><i class="bi bi-download"></i></a> ' +
      '<button type="button" class="btn btn-sm btn-outline-danger fms-attachment-delete" data-attachment-id="' + att.id + '"><i class="bi bi-trash"></i></button>' +
      '</div></li>';
  }

  document.querySelectorAll(".fms-attachment-upload-form").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var card = form.closest(".card");
      var list = card.querySelector(".fms-attachment-list");
      var errorBox = form.querySelector(".fms-upload-error");
      var btn = form.querySelector("button[type=submit]");
      var spinner = btn.querySelector(".spinner-border");
      var label = btn.querySelector(".upload-label");
      errorBox.classList.add("d-none");
      btn.disabled = true;
      spinner.classList.remove("d-none");
      label.textContent = "Uploading...";

      var formData = new FormData(form);
      fetch("/master/attachments/upload", {
        method: "POST",
        body: formData,
        headers: {"X-CSRFToken": getCsrfToken()}
      })
        .then(function (r) {
          return r.json().catch(function () {
            // The server responded but not with JSON — a proxy/timeout
            // page, or something outside our own error handling. Still
            // give the person a real signal about what happened rather
            // than a bare parse failure.
            throw new Error("http_" + r.status);
          });
        })
        .then(function (data) {
          btn.disabled = false;
          spinner.classList.add("d-none");
          label.textContent = "Upload";
          if (!data.ok) {
            errorBox.textContent = data.error || "Upload failed.";
            errorBox.classList.remove("d-none");
            return;
          }
          // Low-resolution scans upload successfully -- this is
          // advice, not an error -- but the person needs it NOW, while
          // the document is still in front of them and rescanning is
          // cheap. Measured on real CR scans: at ~82 DPI, automatic
          // extraction recovers under half the fields and misreads
          // engine and chassis numbers.
          if (data.scan_warning && window.Swal) {
            Swal.fire({icon: "info", title: "Low-resolution scan",
                       text: data.scan_warning,
                       confirmButtonText: "Got it"});
          } else if (data.scan_warning) {
            errorBox.textContent = data.scan_warning;
            errorBox.classList.remove("d-none", "alert-danger");
            errorBox.classList.add("alert-info");
          }

          var emptyRow = list.querySelector("li.text-muted");
          if (emptyRow) emptyRow.remove();
          list.insertAdjacentHTML("beforeend", renderAttachmentRow(data));
          form.reset();
        })
        .catch(function (err) {
          btn.disabled = false;
          spinner.classList.add("d-none");
          label.textContent = "Upload";
          var message = "Upload failed. Please try again.";
          if (err && err.message === "http_401") {
            message = "Your session has expired — please log in again.";
          } else if (err && err.message === "http_413") {
            message = "This file is too large to upload.";
          } else if (err && /^http_5/.test(err.message)) {
            message = "The server encountered an error uploading this file. Please try again or contact your administrator.";
          } else if (!navigator.onLine) {
            message = "You appear to be offline — check your connection and try again.";
          }
          errorBox.textContent = message;
          errorBox.classList.remove("d-none");
        });
    });
  });

  document.addEventListener("click", function (e) {
    var delBtn = e.target.closest(".fms-attachment-delete");
    if (!delBtn) return;
    e.preventDefault();
    var id = delBtn.dataset.attachmentId;
    Swal.fire({title: "Delete this attachment?", icon: "warning",
               showCancelButton: true, confirmButtonText: "Yes"})
      .then(function (result) {
        if (!result.isConfirmed) return;
        fetch("/master/attachments/" + id + "/delete", {
          method: "POST", headers: {"X-CSRFToken": getCsrfToken()}
        }).then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.ok) {
              var row = document.querySelector('li[data-attachment-id="' + id + '"]');
              if (row) row.remove();
            }
          });
      });
  });

  // Comment/Discussion thread (_comment_thread.html): auto-wires the
  // Recipient AJAX select and the Attach File label on every page that
  // includes the partial, so no per-page script block is needed.
  document.querySelectorAll('select[id^="commentRecipientSelect_"]').forEach(function (el) {
    if (window.initAjaxSelect) {
      window.initAjaxSelect("#" + el.id, "/api/search/users",
        { placeholder: "Type a name to notify (optional)...", allowClear: true });
    }
  });
  document.querySelectorAll(".commentFileInput").forEach(function (input) {
    input.addEventListener("change", function () {
      var label = input.closest("form").querySelector(".commentFileLabel");
      if (label) label.textContent = input.files.length + " file(s)";
    });
  });
})();

// Comma-formatted money inputs — e.g. formatMoneyInput("#vehAcquisitionCost").
// Displays "5,000.00" style formatting live as the person types, but strips
// the commas back out right before the form actually submits, so the
// server always receives a plain decimal number.
window.formatMoneyInput = function (selector) {
  document.querySelectorAll(selector).forEach(function (input) {
    if (input.dataset.moneyBound === "1") return;  // idempotent
    input.dataset.moneyBound = "1";
    function formatValue(raw) {
      var cleaned = raw.replace(/[^0-9.]/g, "");
      var firstDot = cleaned.indexOf(".");
      if (firstDot !== -1) {
        cleaned = cleaned.slice(0, firstDot + 1)
          + cleaned.slice(firstDot + 1).replace(/\./g, "");
      }
      var parts = cleaned.split(".");
      var intPart = (parts[0] || "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
      var decPart = parts.length > 1 ? "." + parts[1].slice(0, 2) : "";
      return intPart + decPart;
    }
    if (input.value) { input.value = formatValue(input.value); }
    input.addEventListener("input", function () {
      var cursorFromEnd = input.value.length - input.selectionStart;
      input.value = formatValue(input.value);
      var newPos = Math.max(0, input.value.length - cursorFromEnd);
      input.setSelectionRange(newPos, newPos);
    });
    var form = input.closest("form");
    if (form && !form.dataset.moneyStripBound) {
      form.dataset.moneyStripBound = "1";
      form.addEventListener("submit", function () {
        form.querySelectorAll("[data-money-bound='1']").forEach(function (el) {
          el.value = el.value.replace(/,/g, "");
        });
      });
    }
  });
};

// Auto-apply comma formatting to every currency field marked with the
// `js-money` class, so a person typing "950000" sees "950,000.00" live
// in ANY currency entry field app-wide -- not just the one vehicle
// acquisition-cost field that was wired up by hand originally. New
// currency inputs only need the js-money class (and type="text"
// inputmode="decimal", since an HTML5 type="number" input is spec-
// forbidden from ever displaying a comma at all). Also re-run after
// dynamically added rows (e.g. Purchase Request / invoice line items).
document.addEventListener("DOMContentLoaded", function () {
  if (window.formatMoneyInput) window.formatMoneyInput(".js-money");
});
window.applyMoneyFormatting = function () {
  if (window.formatMoneyInput) window.formatMoneyInput(".js-money");
};

// ── Global error popups ──────────────────────────────────────────────────
// Any AJAX call that fails (server error, permission error, or the request
// never reaching the server at all -- offline, timeout, DNS, etc.) shows a
// SweetAlert2 popup instead of failing silently or leaving a raw browser
// error in the console that only a developer would ever see. The person
// can then screenshot the popup (it includes a reference code for 500s)
// and send it in, rather than trying to describe "it didn't work."
(function () {
  function showErrorPopup(payload, statusCode) {
    var title, text, icon;
    if (statusCode === 0 || payload === null) {
      // The request never got a response at all -- offline, DNS failure,
      // server unreachable, request timed out, etc. Distinct from the
      // server responding WITH an error, since the fix is different
      // (check your connection) vs (something's wrong on our side).
      title = "Connection Problem";
      text = "Could not reach the server. Please check your internet "
           + "connection and try again. If this keeps happening, take a "
           + "screenshot of this message and send it to your administrator.";
      icon = "error";
    } else if (statusCode === 401
               || (payload && payload.error_type === "SESSION_EXPIRED")) {
      // The session timed out while this page sat open. A popup saying
      // "unauthorised" would be a dead end -- the person cannot fix it
      // from here -- so acknowledge it and take them to the login page.
      // The original action is deliberately NOT retried afterwards:
      // silently re-firing something like a Cancel or Approve after a
      // re-login is how an order gets cancelled that nobody meant to
      // cancel.
      Swal.fire({
        title: "Session Expired",
        text: (payload && payload.error)
              || "Your session has expired. Please sign in again.",
        icon: "warning",
        confirmButtonText: "Sign in"
      }).then(function () {
        window.location.href = (payload && payload.login_url) || "/login";
      });
      return;
    } else if (statusCode === 403) {
      title = "Not Allowed";
      text = payload.error || "You don't have permission to do that.";
      icon = "warning";
    } else if (statusCode === 404) {
      title = "Not Found";
      text = payload.error || "That wasn't found.";
      icon = "warning";
    } else {
      title = "Something Went Wrong";
      text = payload.error || "An unexpected error occurred.";
      if (payload.reference) {
        text += "\n\nReference code: " + payload.reference
              + " — please include this if you report the issue.";
      }
      icon = "error";
    }
    if (window.Swal) {
      Swal.fire({ title: title, text: text, icon: icon,
        confirmButtonText: "OK" });
    } else {
      // SweetAlert2 itself failed to load (e.g. offline, CDN blocked) --
      // fall back to a plain alert so the person still sees SOMETHING
      // rather than nothing.
      window.alert(title + "\n\n" + text);
    }
  }

  // jQuery-based calls (DataTables server-side processing, $.ajax/$.post
  // used throughout the app's own AJAX endpoints).
  if (window.jQuery) {
    jQuery(document).ajaxError(function (_event, jqXHR) {
      if (jqXHR.status === 0) {
        showErrorPopup(null, 0);
        return;
      }
      if (jqXHR.status < 400) return; // not actually an error
      var payload = {};
      try { payload = JSON.parse(jqXHR.responseText); } catch (e) { /* not JSON */ }
      showErrorPopup(payload, jqXHR.status);
    });
  }

  // Native fetch() calls -- wrap once, globally, so individual call sites
  // don't each need their own error handling to get this behavior.
  var _origFetch = window.fetch;
  if (_origFetch) {
    window.fetch = function () {
      return _origFetch.apply(this, arguments).then(function (response) {
        if (!response.ok) {
          response.clone().json().then(function (payload) {
            showErrorPopup(payload, response.status);
          }).catch(function () {
            showErrorPopup({}, response.status);
          });
        }
        return response;
      }).catch(function (err) {
        showErrorPopup(null, 0);
        throw err;
      });
    };
  }
})();

/* ── Standard confirmation dialog ──────────────────────────────────────
   One confirmation style for the whole application.

   Before this, three different things were in use: SweetAlert2 in some
   places, a raw browser confirm() in others (which renders as an
   unstyled OS dialog showing the site's IP address and cannot be
   themed), and inline onclick="return confirm(...)" attributes
   elsewhere. Same product, three visual languages, and the browser one
   looked broken next to the rest.

   Two ways to use it:

     1. Declaratively -- add data-confirm="Your question?" to any form
        or link. Nothing else required; the handler below intercepts it.
     2. Programmatically -- window.fmsConfirm("Question?").then(ok => …)
        for cases that need to run code rather than submit a form.

   Destructive actions (delete/remove/cancel wording, or an explicit
   data-confirm-danger) get a red confirm button, because "Remove this
   part?" and "Submit for approval?" should not look identical at the
   moment someone is clicking without fully reading.
*/
(function () {
  "use strict";

  var DESTRUCTIVE = /\b(delete|remove|deactivate|cancel|discard|reject|clear|undo|reset)\b/i;

  function looksDestructive(message, explicit) {
    if (explicit === "true") return true;
    if (explicit === "false") return false;
    return DESTRUCTIVE.test(message || "");
  }

  window.fmsConfirm = function (message, options) {
    options = options || {};
    var danger = looksDestructive(message, options.danger);

    if (!window.Swal) {
      // SweetAlert2 blocked or failed to load. A plain confirm is ugly
      // but it still asks the question -- silently proceeding with a
      // destructive action because a stylesheet didn't load would be
      // far worse.
      return Promise.resolve(window.confirm(message));
    }
    return Swal.fire({
      title: options.title || (danger ? "Please confirm" : "Confirm"),
      text: message,
      icon: options.icon || (danger ? "warning" : "question"),
      showCancelButton: true,
      confirmButtonText: options.confirmText || (danger ? "Yes, continue" : "Confirm"),
      cancelButtonText: options.cancelText || "Cancel",
      confirmButtonColor: danger ? "#dc3545" : "#0d6efd",
      cancelButtonColor: "#6c757d",
      reverseButtons: true,      // Cancel on the left, so the destructive
                                 // button isn't where Cancel usually sits
      focusCancel: danger        // Enter shouldn't confirm a deletion
    }).then(function (result) { return !!result.isConfirmed; });
  };

  // Declarative: data-confirm="..." on a form or link.
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches || !form.matches("[data-confirm]")) return;
    if (form.__fmsConfirmed) { form.__fmsConfirmed = false; return; }
    e.preventDefault();
    window.fmsConfirm(form.getAttribute("data-confirm"),
                      {danger: form.getAttribute("data-confirm-danger")})
      .then(function (ok) {
        if (!ok) return;
        form.__fmsConfirmed = true;
        // requestSubmit keeps native validation and the submitter
        // button's value, which form.submit() would silently discard.
        if (form.requestSubmit) { form.requestSubmit(); }
        else { form.submit(); }
      });
  }, true);

  document.addEventListener("click", function (e) {
    if (!e.target.closest) return;

    // A submit button carrying its own data-confirm. This is a distinct
    // case from the form: a form can have several submit buttons with
    // different formaction targets (Save / Test / Run Backup), and only
    // one of them may warrant a confirmation. Putting data-confirm on
    // the form would prompt for all of them.
    var btn = e.target.closest("button[data-confirm], input[type=submit][data-confirm]");
    if (btn) {
      if (btn.__fmsConfirmed) { btn.__fmsConfirmed = false; return; }
      e.preventDefault();
      e.stopPropagation();
      window.fmsConfirm(btn.getAttribute("data-confirm"),
                        {danger: btn.getAttribute("data-confirm-danger")})
        .then(function (ok) {
          if (!ok) return;
          btn.__fmsConfirmed = true;
          // Re-click the button itself rather than submitting the form,
          // so its formaction and name/value still apply.
          btn.click();
        });
      return;
    }

    var link = e.target.closest("a[data-confirm]");
    if (!link) return;
    e.preventDefault();
    window.fmsConfirm(link.getAttribute("data-confirm"),
                      {danger: link.getAttribute("data-confirm-danger")})
      .then(function (ok) { if (ok) { window.location.href = link.href; } });
  }, true);
})();
