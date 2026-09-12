(function () {
  const bundle = JSON.parse(document.getElementById("review-bundle").textContent);
  const scene = document.getElementById("scene");
  const select = document.getElementById("candidate-select");
  const details = document.getElementById("detail-text");
  const statusLine = document.getElementById("status-line");
  const skeletonLine = document.getElementById("skeleton-line");
  const candidates = bundle.candidates || [];
  let current = 0;
  let view = { x: 0, y: 0, scale: 1 };
  let animationTimer = null;

  function currentEvidence() {
    const candidate = candidates[current] || {};
    const matches = candidate.official === true
      && typeof candidate.candidate_id === "string"
      && candidate.candidate_id === (bundle.official || {}).candidate_id;
    return {
      journeys: matches ? (bundle.journeys || []) : [],
      failures: matches ? (bundle.failures || []) : []
    };
  }
  function stopAnimation() {
    if (animationTimer !== null) clearInterval(animationTimer);
    animationTimer = null;
  }

  function escapeAttr(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }
  function escapeText(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }
  function points(poly) {
    return (poly || []).map(function (p) { return p[0] + "," + p[1]; }).join(" ");
  }
  function boundsOf(candidate) {
    const pts = [];
    (bundle.site.boundary || []).forEach(function (p) { pts.push(p); });
    const geom = (candidate && candidate.geometry) || {};
    (geom.aisles || []).forEach(function (a) { (a.polygon || []).forEach(function (p) { pts.push(p); }); });
    (geom.stalls || []).forEach(function (s) { (s.polygon || []).forEach(function (p) { pts.push(p); }); });
    if (!pts.length) return { minX: 0, minY: 0, maxX: 100, maxY: 100 };
    const xs = pts.map(function (p) { return p[0]; });
    const ys = pts.map(function (p) { return p[1]; });
    return { minX: Math.min.apply(null, xs), minY: Math.min.apply(null, ys), maxX: Math.max.apply(null, xs), maxY: Math.max.apply(null, ys) };
  }
  function render() {
    stopAnimation();
    details.removeAttribute("data-selected");
    const candidate = candidates[current] || { geometry: { aisles: [], stalls: [] }, status: "not_solved" };
    const evidence = currentEvidence();
    document.getElementById("play-journey").disabled = evidence.journeys.length === 0;
    const b = boundsOf(candidate);
    const pad = 3;
    const w = Math.max(b.maxX - b.minX, 1) + pad * 2;
    const h = Math.max(b.maxY - b.minY, 1) + pad * 2;
    scene.setAttribute("viewBox", "0 0 " + w + " " + h);
    scene.setAttribute("data-candidate-id", candidate.candidate_id || "");
    scene.setAttribute("data-status", candidate.status || "");
    const tx = function (p) { return [(p[0] - b.minX + pad), (b.maxY - p[1] + pad)]; };
    const poly = function (arr, cls, extra) {
      const mapped = (arr || []).map(tx);
      return '<polygon class="' + cls + '" points="' + points(mapped) + '" stroke-width="0.12" ' + (extra || "") + "/>";
    };
    let html = poly(bundle.site.boundary, "boundary", 'stroke="#0f172a" fill="#ffffff"');
    (bundle.site.obstacles || []).forEach(function (o) {
      html += poly(o, "obstacle", 'stroke="#991b1b" fill="#fecaca"');
    });
    if (document.getElementById("layer-aisles").checked) {
      (candidate.geometry.aisles || []).forEach(function (a) {
        html += poly(
          a.polygon,
          candidate.official ? "official" : "preview",
          'data-object-id="' + escapeAttr(a.id || "") + '" data-role="' + escapeAttr(a.role || "") + '" data-directionality="' + escapeAttr(a.directionality || "") + '"'
        );
      });
    }
    if (document.getElementById("layer-stalls").checked) {
      (candidate.geometry.stalls || []).forEach(function (s) {
        html += poly(s.polygon, "stall", 'data-object-id="' + escapeAttr(s.id || "") + '" fill="#dbeafe" stroke="#1d4ed8"');
      });
    }
    if (document.getElementById("layer-journeys").checked) {
      evidence.journeys.forEach(function (j) {
        const traj = (j.trajectory || []).map(function (p) {
          if (Array.isArray(p)) return tx(p);
          return tx([p.x, p.y]);
        });
        if (traj.length > 1) {
          html += '<polyline class="journey" data-stall-id="' + escapeAttr(j.stall_id || "") + '" points="' + points(traj) + '" stroke-width="0.1"/>';
        }
      });
    }
    if (document.getElementById("layer-failures").checked) {
      evidence.failures.forEach(function (f) {
        html += '<text class="fail" x="2" y="2" font-size="1.2" data-object-id="' + escapeAttr(f.object_id || "") + '">' + escapeText(f.object_id || f.reason || "fail") + "</text>";
      });
    }
    html += '<circle id="vehicle-marker" class="vehicle" r="0.4" visibility="hidden"/>';
    scene.innerHTML = html;
    statusLine.textContent = "candidate " + (candidate.candidate_id || "?") + " status=" + (candidate.status || "?") + " stalls=" + (candidate.stall_count == null ? "not_evaluated" : candidate.stall_count);
    if (skeletonLine) {
      skeletonLine.textContent = "family=" + (candidate.family || (bundle.official || {}).family || "-")
        + " skeleton=" + (candidate.skeleton_id || (bundle.official || {}).skeleton_id || "-")
        + " mode=" + (candidate.generation_mode || (bundle.official || {}).generation_mode || "-");
    }
    const failedJunctions = evidence.failures.filter(function (f) {
      const reason = String(f.reason || f.kind || "");
      return reason.indexOf("junction") >= 0 || reason.indexOf("undeclared") >= 0;
    });
    details.textContent = JSON.stringify({
      candidate_id: candidate.candidate_id,
      status: candidate.status,
      official: candidate.official,
      family: candidate.family || (bundle.official || {}).family,
      skeleton_id: candidate.skeleton_id || (bundle.official || {}).skeleton_id,
      generation_mode: candidate.generation_mode || (bundle.official || {}).generation_mode,
      aisles: (candidate.geometry.aisles || []).map(function (a) {
        return { id: a.id, role: a.role, directionality: a.directionality };
      }),
      checks_passed: candidate.checks_passed,
      score: candidate.score,
      road_traversal: candidate.road_traversal,
      failed_junctions: failedJunctions
    }, null, 2);
  }
  candidates.forEach(function (c, i) {
    const opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = (c.candidate_id || "candidate") + " [" + (c.status || "") + "]";
    select.appendChild(opt);
  });
  select.addEventListener("change", function () { current = Number(select.value); render(); });
  ["layer-aisles", "layer-stalls", "layer-journeys", "layer-failures"].forEach(function (id) {
    document.getElementById(id).addEventListener("change", render);
  });
  scene.addEventListener("click", function (ev) {
    const target = ev.target;
    const objectId = target.getAttribute("data-object-id") || target.getAttribute("data-stall-id");
    if (!objectId) return;
    target.classList.add("fail");
    const evidence = currentEvidence();
    const fail = evidence.failures.find(function (f) { return f.object_id === objectId; });
    const journey = evidence.journeys.find(function (j) { return j.stall_id === objectId; });
    details.textContent = JSON.stringify({ selected: objectId, failure: fail || null, journey: journey || null }, null, 2);
    details.setAttribute("data-selected", objectId);
  });
  document.getElementById("play-journey").addEventListener("click", function () {
    stopAnimation();
    const selected = details.getAttribute("data-selected");
    const journeys = currentEvidence().journeys;
    const journey = journeys.find(function (j) { return j.stall_id === selected; }) || journeys[0];
    if (!journey || !journey.trajectory || !journey.trajectory.length) return;
    const marker = document.getElementById("vehicle-marker");
    const b = boundsOf(candidates[current]);
    const pad = 3;
    let i = 0;
    marker.setAttribute("visibility", "visible");
    animationTimer = setInterval(function () {
      const p = journey.trajectory[i];
      const xy = Array.isArray(p) ? p : [p.x, p.y];
      marker.setAttribute("cx", String(xy[0] - b.minX + pad));
      marker.setAttribute("cy", String(b.maxY - xy[1] + pad));
      i += 1;
      if (i >= journey.trajectory.length) stopAnimation();
    }, 40);
  });
  let drag = null;
  scene.addEventListener("mousedown", function (ev) { drag = { x: ev.clientX, y: ev.clientY }; });
  window.addEventListener("mouseup", function () { drag = null; });
  scene.addEventListener("mousemove", function (ev) {
    if (!drag) return;
    view.x += ev.clientX - drag.x;
    view.y += ev.clientY - drag.y;
    drag = { x: ev.clientX, y: ev.clientY };
    scene.style.transform = "translate(" + view.x + "px," + view.y + "px) scale(" + view.scale + ")";
  });
  scene.addEventListener("wheel", function (ev) {
    ev.preventDefault();
    view.scale = Math.max(0.2, Math.min(8, view.scale * (ev.deltaY < 0 ? 1.1 : 0.9)));
    scene.style.transform = "translate(" + view.x + "px," + view.y + "px) scale(" + view.scale + ")";
  }, { passive: false });
  render();
})();
