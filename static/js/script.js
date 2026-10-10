(function () {
  const box = document.getElementById("quiz");
  if (!box) return;
  const label = box.dataset.label;
  let list = [];
  function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
  function load(qs) {
    box.innerHTML = '<p class="muted">Loading…</p>';
    fetch("/api/questions?" + qs).then(r => r.json()).then(data => {
      list = data;
      if (!list.length) { box.innerHTML = '<p class="muted">No questions available for this selection.</p>'; return; }
      box.innerHTML = list.map((q, i) => `<div class="q" id="${q.id}"><p class="src">${i + 1}. ${esc(q.src)} · ${esc(q.topic)}</p>` + (q.img ? `<img class="qimg" src="${q.img}" alt="Question ${i + 1}">` : `<p lang="hi">${esc(q.q)}</p>`) +
        q.o.map((o, j) => `<label class="opt"><input type="radio" name="${q.id}" value="${j}"> ${esc(o)}</label>`).join("") +
        '<div class="exp"></div></div>').join("") + '<button class="btn" id="submit">Submit</button>';
      document.getElementById("submit").onclick = submit;
    });
  }
  document.querySelectorAll("[data-load]").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll("[data-load]").forEach(x => { x.classList.remove("active"); x.classList.add("ghost"); });
    b.classList.add("active"); b.classList.remove("ghost"); load(b.dataset.load);
  }));
  function submit(e) {
    const answers = {};
    list.forEach(q => { const c = box.querySelector(`input[name="${q.id}"]:checked`); if (c) answers[q.id] = +c.value; });
    e.target.disabled = true;
    fetch("/api/submit", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: list.map(q => q.id), answers, label }) }).then(r => r.json()).then(res => {
      list.forEach(q => {
        const r = res.results[q.id], el = document.getElementById(q.id), opts = el.querySelectorAll(".opt");
        opts.forEach(o => o.querySelector("input").disabled = true);
        opts[r.a].classList.add("correct");
        if (!r.ok && answers[q.id] !== undefined) opts[answers[q.id]].classList.add("wrong");
        el.querySelector(".exp").textContent = (r.ok ? "Correct. " : answers[q.id] === undefined ? "Skipped. " : "Wrong. ") + r.e;
      });
      box.insertAdjacentHTML("afterbegin", `<div class="score">Score: ${res.score} / ${res.total}</div>`);
      box.insertAdjacentHTML("beforeend", '<p><a class="btn" href="/quiz?mode=wrong">Retry wrong</a> <a class="btn ghost" href="/practice">New practice</a></p>');
      e.target.remove(); window.scrollTo(0, 0);
    });
  }
  load(box.dataset.qs);
})();
