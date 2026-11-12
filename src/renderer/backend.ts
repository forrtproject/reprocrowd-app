// When the app's backend can't be used: what is wrong, the end of its output, and Restart. Used by the package view's
// "can't open" screen and the library. Checks again every few seconds and calls onOk once the backend answers.
import { host } from "./api";
import { esc } from "./util";

export const TITLES: Record<string, string> = {
  backend_start_failed: "The ReproCrowd backend couldn't be started",
  backend_unreachable: "The ReproCrowd backend stopped answering",
  backend_timeout: "The ReproCrowd backend isn't responding",
};

const KIND = { bundled: "the backend bundled with the app", development: "the development checkout (uv run, ../reprocrowd-cli)", custom: "the start command from Settings" };

export async function backendProblem(el: HTMLElement, onOk: () => void) {
  const render = async () => {
    const [st, logText] = await Promise.all([host.backendStatus(), host.backendLog().catch(() => "")]);
    el.innerHTML = `
      <p>${st.state === "starting" ? "It is starting…" : esc(st.message ?? "It isn't running.")}</p>
      <p class="hint">ReproCrowd does all package work in its backend, which it starts by itself (${esc(st.kind ? KIND[st.kind] : "bundled, or set in Settings → Backend")}). Your packages and their files are safe.</p>
      ${logText ? `<details><summary>Backend output</summary><pre class="log">${esc(logText)}</pre></details>` : ""}
      <div class="actions"><button class="primary" id="bp-restart">Restart the backend</button><span class="muted small">Checking again every few seconds…</span></div>`;
    el.querySelector("#bp-restart")!.addEventListener("click", async (e) => {
      (e.target as HTMLButtonElement).disabled = true;
      await host.restartBackend().catch(() => {});
      check();
    });
  };
  const check = async () => {
    const c = await host.checkBackend().catch(() => undefined);
    if (c?.ok) {
      clearInterval(timer);
      onOk();
    } else await render();
  };
  const timer = window.setInterval(check, 5000);
  await render();
}
