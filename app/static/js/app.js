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

  // Sidebar collapse
  const wrapper = document.getElementById("fms-wrapper");
  if (wrapper && getCookie("fms-sidebar") === "collapsed") {
    wrapper.classList.add("sidebar-collapsed");
  }
  const sbToggle = document.getElementById("sidebarToggle");
  if (sbToggle) {
    sbToggle.addEventListener("click", function () {
      wrapper.classList.toggle("sidebar-collapsed");
      setCookie("fms-sidebar",
        wrapper.classList.contains("sidebar-collapsed") ? "collapsed" : "open");
    });
  }

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
        .then(function (r) { return r.json(); })
        .then(function (data) {
          btn.disabled = false;
          spinner.classList.add("d-none");
          label.textContent = "Upload";
          if (!data.ok) {
            errorBox.textContent = data.error || "Upload failed.";
            errorBox.classList.remove("d-none");
            return;
          }
          var emptyRow = list.querySelector("li.text-muted");
          if (emptyRow) emptyRow.remove();
          list.insertAdjacentHTML("beforeend", renderAttachmentRow(data));
          form.reset();
        })
        .catch(function () {
          btn.disabled = false;
          spinner.classList.add("d-none");
          label.textContent = "Upload";
          errorBox.textContent = "Upload failed. Please try again.";
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
