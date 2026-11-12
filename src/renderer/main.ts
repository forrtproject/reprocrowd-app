// Entry: the library, or one package's workflow (#pkg=<id>). Each view renders into #app.
import "@fontsource/source-sans-3/400.css";
import "@fontsource/source-sans-3/600.css";
import "@fontsource/source-sans-3/700.css";
import "@fontsource/domine/500.css";
import "@fontsource/domine/600.css";
import "@fontsource/domine/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "./style.css";

const id = new URLSearchParams(location.hash.slice(1)).get("pkg");
document.body.classList.add(id ? "view-package" : "view-library");

// The ReproCrowd mark in the header leads back to the library.
document.addEventListener("click", (e) => {
  if (!(e.target as HTMLElement).closest("[data-brand]")) return;
  e.preventDefault();
  if (id) document.querySelector<HTMLButtonElement>("#library-btn")?.click();
});

if (id) import("./package").then((m) => m.start(id));
else import("./library").then((m) => m.start());
