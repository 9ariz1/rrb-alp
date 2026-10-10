(function () {
  const f = document.getElementById("qform"); if (!f) return;
  const subj = document.getElementById("subject"), top = document.getElementById("topic"), type = document.getElementById("qtype"), box = document.getElementById("paperbox");
  function topics() { [...top.options].forEach(o => { if (o.value) { o.hidden = o.disabled = o.dataset.subject !== subj.value; } }); if (top.selectedOptions[0] && top.selectedOptions[0].disabled) top.value = ""; }
  function paper() { box.hidden = type.value !== "previous_paper"; }
  subj.addEventListener("change", topics); type.addEventListener("change", paper); topics(); paper();
})();
