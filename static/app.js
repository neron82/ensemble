"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  characters: [],
  rooms: [],
  selected: null,
  roomsState: new Map(),
  services: null,
  navigating: 0,
  polling: new Map(),
  audio: new Map(),
  queue: [],
  player: null,
  playing: null,
  audioEpoch: 0,
  dialogEpoch: 0,
};
const sameId = (a, b) => String(a) === String(b);
const character = (id) => state.characters.find((c) => sameId(c.id, id));
const selectedRoom = () =>
  state.rooms.find((r) => sameId(r.id, state.selected));
const roomState = (id) => {
  const key = String(id);
  if (!state.roomsState.has(key))
    state.roomsState.set(key, {
      messages: [],
      loaded: false,
      run: null,
      busy: false,
      draft: "",
      target: "",
      budget: null,
      error: "",
      request: 0,
    });
  return state.roomsState.get(key);
};
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function button(text, className, action, label) {
  const node = el("button", className, text);
  node.type = "button";
  if (label) node.setAttribute("aria-label", label);
  node.addEventListener("click", action);
  return node;
}
function option(text, value) {
  const node = el("option", "", text);
  node.value = value;
  return node;
}
function safeMedia(url) {
  return typeof url === "string" &&
    /^\/(?:media\/[A-Za-z0-9_.-]+|static\/avatars\/[A-Za-z0-9_-]+\.svg)$/.test(
      url,
    )
    ? url
    : "";
}
function avatar(c = {}, size = "") {
  const node = el("span", `avatar ${size}`);
  const name = c.name || c.speaker_name || "Ensemble";
  node.textContent = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((v) => v[0])
    .join("")
    .toUpperCase();
  if (/^#[0-9a-f]{6}$/i.test(c.color || ""))
    node.style.setProperty("--avatar-color", c.color);
  const url = safeMedia(c.icon_url);
  if (url) {
    const image = el("img", `avatar ${size}`);
    image.src = url;
    image.alt = "";
    image.loading = "lazy";
    image.addEventListener("error", () => image.replaceWith(node), {
      once: true,
    });
    return image;
  }
  node.setAttribute("aria-hidden", "true");
  return node;
}
async function api(path, method = "GET", body) {
  const controller = new AbortController();
  const timer = setTimeout(
    () => controller.abort(),
    path.endsWith("/audio") ? 180000 : 45000,
  );
  try {
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      headers:
        body instanceof FormData || body === undefined
          ? {}
          : { "Content-Type": "application/json" },
      body:
        body === undefined
          ? undefined
          : body instanceof FormData
            ? body
            : JSON.stringify(body),
    });
    const text = await response.text();
    let data;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      throw new Error(
        `Server returned an unreadable response (${response.status}).`,
      );
    }
    if (!response.ok) {
      const detail = data?.detail || data?.error;
      const error = new Error(
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail
                .map(
                  (d) => `${d.loc?.slice(1).join(".") || "Request"}: ${d.msg}`,
                )
                .join("\n")
            : `Request failed (${response.status}).`,
      );
      error.status = response.status;
      throw error;
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError")
      throw new Error(
        "The request timed out. Check the local service and try again.",
      );
    if (error instanceof TypeError)
      throw new Error(
        "Cannot reach Ensemble. Check that the local app is running, then retry.",
      );
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
let toastTimer;
function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").hidden = true), 4200);
}
function showError(error, id = state.selected) {
  if (id !== null) roomState(id).error = error.message || String(error);
  if (sameId(id, state.selected)) renderControls();
  else toast(error.message || String(error));
}
function savePreference(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {}
}
function preference(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function renderNavigation() {
  $("room-count").textContent = state.rooms.filter(
    (r) => r.kind === "group",
  ).length;
  for (const kind of ["group", "direct"]) {
    const host = $(kind === "group" ? "group-rooms" : "direct-rooms");
    host.replaceChildren();
    for (const room of state.rooms.filter((r) => r.kind === kind)) {
      const node = button(
        "",
        `nav-room${sameId(room.id, state.selected) ? " active" : ""}`,
        () => selectRoom(room.id),
      );
      node.setAttribute(
        "aria-current",
        sameId(room.id, state.selected) ? "page" : "false",
      );
      node.append(
        kind === "direct"
          ? avatar(character(room.character_ids[0]), "small")
          : el("span", "room-glyph", "◇"),
        el("span", "nav-room-title", room.title),
      );
      if (roomState(room.id).run?.status === "running")
        node.append(el("span", "status-dot running"));
      host.append(node);
    }
    if (!host.children.length)
      host.append(
        el(
          "p",
          "nav-empty",
          kind === "group"
            ? "A new world is one room away."
            : "Meet someone in the character library.",
        ),
      );
  }
}
function renderRoom() {
  const room = selectedRoom();
  $("room-title").textContent = room?.title || "Make room for a story.";
  $("room-kind").textContent = room
    ? room.kind === "direct"
      ? "ONE ON ONE · A PRIVATE THREAD"
      : "GROUP ROOM · SHARED IMAGINATION"
    : "THE CONVERSATION SPACE";
  $("scene-description").textContent =
    room?.description ||
    "No script. No right answer. Give your cast a place, a dilemma, or a wonderfully inconvenient question.";
  const cast = (room?.character_ids || []).map(character).filter(Boolean);
  $("cast-count").textContent = cast.length;
  $("cast-list").replaceChildren();
  for (const [index, c] of cast.entries()) {
    const card = el("div", "cast-card");
    const details = el("div", "cast-details");
    details.append(
      el("span", "cast-name", c.name),
      el(
        "span",
        "cast-handle",
        `${String(index + 1).padStart(2, "0")} / @${c.handle}`,
      ),
    );
    const actions = el("div", "cast-actions");
    actions.append(
      button("Edit character", "text-button", () => characterEditor(c)),
      button("1:1 chat ↗", "text-button", () => openDirect(c)),
    );
    details.append(actions);
    card.append(avatar(c), details);
    $("cast-list").append(card);
  }
  if (!cast.length)
    $("cast-list").append(
      el(
        "p",
        "field-help",
        "Invite characters with Manage, or create someone entirely new in the library.",
      ),
    );
  const rs = roomState(state.selected);
  const target = $("target-select");
  target.replaceChildren(
    option(
      room?.kind === "direct"
        ? cast[0]?.name || "Direct character"
        : "The whole cast",
      "",
    ),
  );
  if (room?.kind !== "direct")
    for (const c of cast) target.append(option(c.name, c.id));
  if (!cast.some((c) => sameId(c.id, rs.target))) rs.target = "";
  target.value = rs.target;
  $("turn-limit").value = rs.budget ?? room?.turn_limit ?? 3;
  $("message-input").value = rs.draft;
  renderMessages(true);
  renderNavigation();
  renderControls();
}
function renderControls() {
  const room = selectedRoom();
  const rs = roomState(state.selected);
  const running = rs.run?.status === "running";
  const unavailable = !room || !rs.loaded || !room.character_ids.length;
  $("send-button").disabled =
    unavailable || running || rs.busy || !$("message-input").value.trim();
  $("continue-button").disabled =
    unavailable ||
    running ||
    rs.busy ||
    !rs.messages.some((m) => m.role !== "error");
  $("continue-button").hidden = running;
  $("stop-button").hidden = !running;
  $("stop-button").disabled = rs.busy;
  $("target-select").disabled =
    unavailable || running || rs.busy || room?.kind === "direct";
  $("turn-limit").disabled = !room || running || rs.busy;
  $("message-input").disabled = !room;
  for (const id of ["edit-room", "edit-scene", "manage-cast", "delete-room"])
    $(id).disabled = !room || running || rs.busy;
  $("page-error").textContent = rs.error;
  $("page-error").hidden = !rs.error;
  $("room-state").textContent = running
    ? "The scene is unfolding"
    : rs.busy
      ? "Connecting…"
      : `${room?.character_ids.length || 0} voices · your pace`;
  $("run-panel").hidden = !rs.run;
  if (rs.run) {
    const run = rs.run;
    const speaker = character(run.current_character_id)?.name;
    $("run-label").textContent = running
      ? speaker
        ? `${speaker} is composing…`
        : "The cast is thinking…"
      : run.status === "stopped"
        ? "Stopped by you · completed replies saved"
        : run.status === "error"
          ? "Conversation paused · generation error"
          : "Reply cap reached · the next move is yours";
    $("run-count").textContent =
      `${run.completed_turns || 0} / ${run.turn_limit} total replies`;
    $("run-progress").max = run.turn_limit;
    $("run-progress").value = run.completed_turns || 0;
    $("run-error").textContent = run.error || "";
    $("run-error").hidden = !run.error;
  }
}
function renderEmpty() {
  const host = $("empty-state");
  const room = selectedRoom();
  const rs = roomState(state.selected);
  host.replaceChildren();
  if (rs.messages.length) {
    host.hidden = true;
    return;
  }
  host.hidden = false;
  const scene = el("section", "empty-scene");
  scene.append(
    el("div", "ensemble-orb"),
    el(
      "div",
      "scene-kicker",
      room ? "THE STAGE IS YOURS" : "A LITTLE WORLD. A LIVING CONVERSATION.",
    ),
  );
  scene.append(
    el(
      "h2",
      "",
      room
        ? room.kind === "direct"
          ? "One voice. Endless possibility."
          : "What happens next\nis up to you."
        : "Extraordinary company.\nUnscripted possibilities.",
    ),
  );
  scene.append(
    el(
      "p",
      "",
      room
        ? "Bring a question, set a scene, or drop your cast into the unexpected. They’ll take it from here—one reply at a time."
        : "Build a cast of characters, give them a world, and see where the conversation goes. Entirely on your machine.",
    ),
  );
  if (!room)
    scene.append(
      button("Create your first room →", "button", () => roomEditor()),
    );
  else if (!rs.loaded)
    scene.append(el("p", "field-help", "Opening the conversation…"));
  else {
    const grid = el("div", "starter-grid");
    for (const [label, prompt] of [
      [
        "A MORAL TANGLE",
        "You can save a kingdom by telling one perfect lie. Do you tell it? Debate it in character.",
      ],
      [
        "AN UNEXPECTED ARRIVAL",
        "The door opens. A stranger places a sealed letter on the table: “One of you wrote this tomorrow.” What happens next?",
      ],
      [
        "AFTER HOURS",
        "The crisis is over. For once, nobody needs to be a hero. What do you talk about over a late-night drink?",
      ],
      [
        "WORLDS COLLIDE",
        "You are stranded together on a ship with one working escape pod. Make a plan—and try to agree.",
      ],
    ]) {
      const node = button("", "starter", () => {
        $("message-input").value = prompt;
        rs.draft = prompt;
        $("message-input").focus();
        renderControls();
      });
      node.append(el("span", "", label), document.createTextNode(prompt));
      grid.append(node);
    }
    scene.append(grid);
  }
  host.append(scene);
}
function messageNode(message) {
  const node = el("article", `message ${message.role}`);
  node.dataset.id = message.id;
  const identity =
    message.role === "user" ? { name: "You", color: "#91a7bd" } : message;
  node.append(avatar(identity));
  const body = el("div", "message-body");
  const meta = el("div", "message-meta");
  meta.append(
    el(
      "span",
      "speaker-name",
      message.role === "user" ? "You" : message.speaker_name || "Ensemble",
    ),
  );
  if (message.role === "assistant")
    meta.append(el("span", "message-tag", "CHARACTER"));
  if (message.created_at) {
    const time = new Date(message.created_at);
    if (!Number.isNaN(time.valueOf())) {
      const t = el(
        "time",
        "message-time",
        time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      );
      t.dateTime = time.toISOString();
      meta.append(t);
    }
  }
  body.append(meta, el("div", "message-content", message.content));
  if (message.role === "assistant" && message.content) {
    const controls = el("div", "message-audio");
    controls.append(
      button("▷ Listen", "text-button audio-button", () =>
        playMessage(message, false),
      ),
      el("span", "audio-error"),
    );
    body.append(controls);
  }
  node.append(body);
  return node;
}
function renderMessages(force = false) {
  const rs = roomState(state.selected);
  const host = $("messages");
  const scroll = $("conversation-scroll");
  const atBottom =
    scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 110;
  if (force) host.replaceChildren();
  const existing = new Map(Array.from(host.children, (n) => [n.dataset.id, n]));
  for (const m of rs.messages) {
    let node = existing.get(String(m.id));
    if (!node) {
      node = messageNode(m);
      host.append(node);
    }
    existing.delete(String(m.id));
  }
  for (const node of existing.values()) node.remove();
  renderEmpty();
  renderAudioControls();
  if (force || atBottom)
    requestAnimationFrame(() => (scroll.scrollTop = scroll.scrollHeight));
}
async function refreshMessages(id, allowAutoplay = false) {
  const rs = roomState(id);
  const request = ++rs.request;
  const messages = await api(`/api/rooms/${encodeURIComponent(id)}/messages`);
  if (request !== rs.request) return;
  const seen = new Set(rs.messages.map((m) => String(m.id)));
  const hadLoaded = rs.loaded;
  rs.messages = messages;
  rs.loaded = true;
  if (sameId(id, state.selected)) {
    renderMessages();
    renderControls();
    if (allowAutoplay && hadLoaded && $("auto-play").checked) {
      for (const m of messages)
        if (m.role === "assistant" && !seen.has(String(m.id)))
          state.queue.push(m);
      processQueue();
    }
  }
}
async function refreshData() {
  const [characters, rooms] = await Promise.all([
    api("/api/characters"),
    api("/api/rooms"),
  ]);
  state.characters = characters;
  state.rooms = rooms;
  for (const room of rooms) {
    const rs = roomState(room.id);
    if (room.active_run) {
      rs.run = room.active_run;
      if (rs.run.status === "running") startPolling(room.id, rs.run.id);
    }
  }
  renderNavigation();
}
async function selectRoom(id) {
  const token = ++state.navigating;
  if (state.selected !== null) {
    roomState(state.selected).draft = $("message-input").value;
    roomState(state.selected).target = $("target-select").value;
  }
  stopAudio();
  closeDrawers();
  state.selected = id;
  savePreference("ensemble.room", id ?? "");
  renderRoom();
  if (id === null) return;
  try {
    await refreshMessages(id);
    if (token !== state.navigating) return;
    const room = selectedRoom();
    if (room?.active_run?.status === "running")
      startPolling(id, room.active_run.id);
  } catch (error) {
    showError(error, id);
  }
}
function startPolling(id, runId) {
  const key = String(id);
  if (state.polling.get(key)?.runId === runId) return;
  const entry = { runId, timer: null };
  const old = state.polling.get(key);
  if (old) clearTimeout(old.timer);
  state.polling.set(key, entry);
  const tick = async () => {
    if (state.polling.get(key) !== entry) return;
    try {
      const run = await api(`/api/runs/${encodeURIComponent(runId)}/status`);
      if (state.polling.get(key) !== entry) return;
      const rs = roomState(id);
      rs.run = run;
      rs.error = "";
      await refreshMessages(id, true);
      if (sameId(id, state.selected)) renderControls();
      renderNavigation();
      if (run.status !== "running") {
        state.polling.delete(key);
        const room = state.rooms.find((r) => sameId(r.id, id));
        if (room) room.active_run = null;
        return;
      }
    } catch (error) {
      if (state.polling.get(key) !== entry) return;
      if (error.status === 404) {
        state.polling.delete(key);
        const rs = roomState(id);
        rs.run = { ...rs.run, status: "stopped", current_character_id: null };
        showError(
          new Error(
            "This run ended when the app restarted. Saved messages are intact; send or continue when ready.",
          ),
          id,
        );
        try {
          await refreshData();
          await refreshMessages(id);
        } catch (refreshError) {
          showError(refreshError, id);
        }
        return;
      }
      showError(new Error(`${error.message} Retrying connection…`), id);
    }
    if (state.polling.get(key) === entry) entry.timer = setTimeout(tick, 1100);
  };
  tick();
}
async function launchRun(continuing = false) {
  const room = selectedRoom();
  if (!room) return;
  const id = room.id;
  const rs = roomState(id);
  if (rs.busy || rs.run?.status === "running") return;
  const limit = Number($("turn-limit").value);
  if (!Number.isInteger(limit) || limit < 1 || limit > 24) {
    showError(new Error("Choose a total reply cap from 1 to 24."));
    $("turn-limit").focus();
    return;
  }
  const content = $("message-input").value.trim();
  if (!continuing && !content) return;
  const target = $("target-select").value;
  const body = {
    turn_limit: limit,
    target_character_id: target ? (character(target)?.id ?? target) : null,
  };
  if (!continuing) body.content = content;
  rs.busy = true;
  rs.error = "";
  rs.budget = limit;
  renderControls();
  try {
    const result = await api(
      `/api/rooms/${encodeURIComponent(id)}/${continuing ? "continue" : "send"}`,
      "POST",
      body,
    );
    if (!continuing) {
      if (rs.draft.trim() === content) rs.draft = "";
      if (
        sameId(id, state.selected) &&
        $("message-input").value.trim() === content
      )
        $("message-input").value = "";
    }
    rs.run = {
      id: result.run_id,
      room_id: id,
      status: "running",
      completed_turns: 0,
      turn_limit: limit,
    };
    room.active_run = rs.run;
    startPolling(id, result.run_id);
  } catch (error) {
    showError(error, id);
    try {
      await refreshData();
      await refreshMessages(id);
    } catch {}
  } finally {
    rs.busy = false;
    if (sameId(id, state.selected)) {
      renderControls();
      $("message-input").focus();
    }
  }
}
async function stopRun() {
  const room = selectedRoom();
  if (!room) return;
  const rs = roomState(room.id);
  rs.busy = true;
  renderControls();
  try {
    const run = await api(
      `/api/rooms/${encodeURIComponent(room.id)}/stop`,
      "POST",
      {},
    );
    if (run?.status) rs.run = run;
    await refreshMessages(room.id);
    stopAudio();
  } catch (error) {
    showError(error, room.id);
  } finally {
    rs.busy = false;
    if (sameId(room.id, state.selected)) renderControls();
  }
}

function renderAudioControls() {
  const rs = roomState(state.selected);
  for (const node of $("messages").children) {
    const audioButton = node.querySelector(".audio-button");
    if (!audioButton) continue;
    const audio = state.audio.get(node.dataset.id) || {};
    const playing =
      state.playing &&
      sameId(state.playing.id, node.dataset.id) &&
      state.player &&
      !state.player.paused;
    audioButton.textContent = audio.loading
      ? "Preparing voice…"
      : playing
        ? "Ⅱ Pause voice"
        : "▷ Listen";
    audioButton.disabled = !!audio.loading;
    node.querySelector(".audio-error").textContent = audio.error || "";
  }
  $("playback-bar").hidden = !state.playing;
  if (state.playing) {
    $("playback-label").textContent =
      `${state.playing.speaker_name} · ${state.player?.paused ? "Paused" : "Now speaking"}`;
    $("pause-audio").textContent = state.player?.paused ? "Resume" : "Pause";
  }
}
function stopAudio() {
  state.audioEpoch++;
  state.queue = [];
  if (state.player) {
    state.player.pause();
    state.player.removeAttribute("src");
    state.player.load();
  }
  state.player = null;
  state.playing = null;
  renderAudioControls();
}
async function processQueue() {
  if (state.playing || !state.queue.length || !$("auto-play").checked) return;
  const message = state.queue.shift();
  if (sameId(message.room_id, state.selected)) await playMessage(message, true);
}
async function playMessage(message, queued) {
  const key = String(message.id);
  if (state.playing && sameId(state.playing.id, message.id) && state.player) {
    if (!state.player.paused) state.player.pause();
    else
      try {
        await state.player.play();
      } catch (error) {
        state.audio.set(key, {
          error: `Playback was blocked. Press Listen to retry. ${error.message}`,
        });
      }
    renderAudioControls();
    return;
  }
  if (!queued) {
    state.queue = [];
    state.audioEpoch++;
    if (state.player) state.player.pause();
    state.player = null;
    state.playing = null;
  }
  const epoch = state.audioEpoch;
  const viewed = state.selected;
  const current = state.audio.get(key) || {};
  if (current.loading) return;
  state.audio.set(key, { ...current, loading: true, error: "" });
  state.playing = message;
  renderAudioControls();
  try {
    let url = safeMedia(current.url || message.audio_url);
    if (!url) {
      const result = await api(
        `/api/messages/${encodeURIComponent(message.id)}/audio`,
        "POST",
        {},
      );
      url = safeMedia(result.audio_url);
      if (!url)
        throw new Error("The server returned an invalid audio address.");
    }
    state.audio.set(key, { url, loading: false, error: "" });
    if (epoch !== state.audioEpoch || !sameId(viewed, state.selected)) return;
    const audio = new Audio(url);
    state.player = audio;
    const fail = (error) => {
      state.audio.set(key, {
        url,
        error: error.message || "Audio could not be played. Try Listen again.",
      });
      if (state.player === audio) {
        state.player = null;
        state.playing = null;
        state.queue = [];
      }
      renderAudioControls();
    };
    audio.addEventListener("ended", () => {
      if (state.player !== audio) return;
      state.player = null;
      state.playing = null;
      renderAudioControls();
      processQueue();
    });
    audio.addEventListener("error", () =>
      fail(new Error("The generated audio could not be decoded or loaded.")),
    );
    audio.addEventListener("pause", renderAudioControls);
    audio.addEventListener("play", renderAudioControls);
    try {
      await audio.play();
    } catch (error) {
      fail(
        new Error(
          `Playback could not start. Press Listen to retry. ${error.message}`,
        ),
      );
    }
  } catch (error) {
    state.audio.set(key, { loading: false, error: error.message });
    if (epoch === state.audioEpoch) {
      state.playing = null;
      state.player = null;
      state.queue = [];
    }
  } finally {
    renderAudioControls();
  }
}

let dialogReturnFocus = null;
function dialog(title) {
  closeDrawers();
  const host = $("editor-dialog");
  if (!host.open) dialogReturnFocus = document.activeElement;
  state.dialogEpoch++;
  $("dialog-title").textContent = title;
  $("dialog-body").replaceChildren();
  if (!host.open) host.showModal();
  host.scrollTop = 0;
  return $("dialog-body");
}
function closeDialog() {
  $("editor-dialog").close();
  state.dialogEpoch++;
  if (dialogReturnFocus?.isConnected) dialogReturnFocus.focus();
}
let fieldId = 0;
function field(label, value = "", type = "text", help = "") {
  const wrap = el("div", "field");
  const id = `field-${++fieldId}`;
  const caption = el("label", "", label);
  caption.htmlFor = id;
  const input = el(type === "textarea" ? "textarea" : "input");
  input.id = id;
  if (type !== "textarea") input.type = type;
  else input.rows = 4;
  input.value = value ?? "";
  wrap.append(caption, input);
  if (help) {
    const hint = el("p", "field-help", help);
    hint.id = `${id}-help`;
    input.setAttribute("aria-describedby", hint.id);
    wrap.append(hint);
  }
  return { wrap, input };
}
function checkbox(label, checked = false) {
  const wrap = el("label", "checkbox-line");
  const input = el("input");
  input.type = "checkbox";
  input.checked = checked;
  wrap.append(input, document.createTextNode(label));
  return { wrap, input };
}
function row(...fields) {
  const node = el("div", "field-row");
  node.append(...fields.map((f) => f.wrap || f));
  return node;
}
function formError(form) {
  const node = el("p", "inline-error");
  node.setAttribute("role", "alert");
  node.hidden = true;
  form.append(node);
  return (error) => {
    node.textContent = error.message || String(error);
    node.hidden = false;
    node.scrollIntoView({ block: "nearest" });
  };
}
function formBusy(form, busy) {
  for (const input of form.querySelectorAll(
    "button, input, textarea, select",
  )) {
    if (busy) {
      input.dataset.wasDisabled = String(input.disabled);
      input.disabled = true;
    } else {
      input.disabled = input.dataset.wasDisabled === "true";
      delete input.dataset.wasDisabled;
    }
  }
}
async function reloadAfterMutation() {
  await refreshData();
  if (state.selected !== null && !selectedRoom())
    state.selected = state.rooms[0]?.id ?? null;
  renderRoom();
}
function confirmDialog(title, description, action, returnAction) {
  const host = dialog(title);
  host.append(el("p", "field-help", description));
  const form = el("form", "editor-form");
  const report = formError(form);
  const actions = el("div", "dialog-actions");
  actions.append(
    button("Cancel", "button secondary", () =>
      returnAction ? returnAction() : closeDialog(),
    ),
  );
  const submit = el("button", "button danger", "Delete permanently");
  submit.type = "submit";
  actions.append(submit);
  form.append(actions);
  host.append(form);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    formBusy(form, true);
    try {
      await action();
    } catch (error) {
      report(error);
      formBusy(form, false);
    }
  });
}
function deleteRoom() {
  const room = selectedRoom();
  if (!room) return;
  confirmDialog(
    "Close this chapter?",
    `Delete “${room.title}” and its message history? Your characters will stay in the library. This cannot be undone.`,
    async () => {
      await api(`/api/rooms/${encodeURIComponent(room.id)}`, "DELETE");
      const poll = state.polling.get(String(room.id));
      if (poll) clearTimeout(poll.timer);
      state.polling.delete(String(room.id));
      state.roomsState.delete(String(room.id));
      await refreshData();
      closeDialog();
      await selectRoom(state.rooms[0]?.id ?? null);
      toast("Conversation deleted.");
    },
  );
}
function roomEditor(existing = null) {
  const host = dialog(existing ? "Shape this room" : "A new world awaits");
  const form = el("form", "editor-form");
  host.append(form);
  const title = field("Room name", existing?.title || "");
  title.input.required = true;
  title.input.maxLength = 100;
  title.input.placeholder = "The observation deck";
  const description = field(
    "The scene",
    existing?.description || "",
    "textarea",
    "Where are we? What is at stake? This context guides every character.",
  );
  description.input.placeholder =
    "Beyond the windows, an unfamiliar planet. Around the table, very different ideas about what comes next…";
  description.input.maxLength = 6000;
  const limit = field(
    "Default total reply cap",
    existing?.turn_limit ?? 3,
    "number",
    "Total assistant replies per run—not replies per character. You can change this before each send.",
  );
  limit.input.min = 1;
  limit.input.max = 24;
  limit.input.required = true;
  form.append(title.wrap, description.wrap, limit.wrap);
  let selected = [
    ...(existing?.character_ids ||
      state.characters.slice(0, 3).map((c) => c.id)),
  ];
  const members = el("div", "member-list");
  const memberHeading = el("div", "field");
  memberHeading.append(
    el(
      "span",
      "field-label",
      existing?.kind === "direct"
        ? "Your conversation partner"
        : "Choose the cast · drag-free speaking order",
    ),
    el(
      "p",
      "field-help",
      existing?.kind === "direct"
        ? "A direct conversation has exactly one character."
        : "Selected characters speak from top to bottom. Use the arrows to change the order.",
    ),
    members,
  );
  form.append(memberHeading);
  function renderMembers() {
    members.replaceChildren();
    const ordered = [
      ...selected.map(character).filter(Boolean),
      ...state.characters.filter(
        (c) => !selected.some((id) => sameId(id, c.id)),
      ),
    ];
    for (const c of ordered) {
      const index = selected.findIndex((id) => sameId(id, c.id));
      const node = el("div", "member-row");
      const input = el("input");
      input.type = existing?.kind === "direct" ? "radio" : "checkbox";
      input.name = "room-member";
      input.id = `member-${c.id}`;
      input.checked = index >= 0;
      input.addEventListener("change", () => {
        if (existing?.kind === "direct") selected = [c.id];
        else if (input.checked) selected.push(c.id);
        else selected = selected.filter((id) => !sameId(id, c.id));
        renderMembers();
        members.querySelector(`#${CSS.escape(input.id)}`)?.focus();
      });
      const label = el("label");
      label.htmlFor = input.id;
      label.append(avatar(c, "small"), document.createTextNode(c.name));
      node.append(
        input,
        el("span", "member-order", index >= 0 ? index + 1 : "—"),
        label,
      );
      if (existing?.kind !== "direct")
        for (const [symbol, direction] of [
          ["↑", -1],
          ["↓", 1],
        ]) {
          const move = button(
            symbol,
            "icon-button",
            () => {
              const to = index + direction;
              [selected[index], selected[to]] = [selected[to], selected[index]];
              renderMembers();
            },
            `Move ${c.name} ${direction < 0 ? "earlier" : "later"}`,
          );
          move.disabled =
            index < 0 ||
            index + direction < 0 ||
            index + direction >= selected.length;
          node.append(move);
        }
      members.append(node);
    }
    if (!ordered.length)
      members.append(
        button("Create a character first →", "text-button", () =>
          characterEditor(),
        ),
      );
  }
  renderMembers();
  const report = formError(form);
  const actions = el("div", "dialog-actions");
  actions.append(button("Cancel", "button secondary", closeDialog));
  const save = el("button", "button", existing ? "Save room" : "Create room");
  save.type = "submit";
  actions.append(save);
  form.append(actions);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selected.length) {
      report(new Error("Choose at least one character for this room."));
      return;
    }
    formBusy(form, true);
    try {
      const result = await api(
        existing
          ? `/api/rooms/${encodeURIComponent(existing.id)}`
          : "/api/rooms",
        existing ? "PUT" : "POST",
        {
          title: title.input.value.trim(),
          description: description.input.value.trim(),
          kind: existing?.kind || "group",
          character_ids: selected,
          turn_limit: Number(limit.input.value),
        },
      );
      await refreshData();
      roomState(result.id).budget = result.turn_limit;
      closeDialog();
      await selectRoom(result.id);
      toast(existing ? "Room updated." : "The stage is set.");
    } catch (error) {
      report(error);
      formBusy(form, false);
    }
  });
  title.input.focus();
}
const openingDirect = new Set();
async function openDirect(c) {
  if (openingDirect.has(String(c.id))) return;
  openingDirect.add(String(c.id));
  try {
    await refreshData();
    let room = state.rooms.find(
      (r) =>
        r.kind === "direct" &&
        r.character_ids.length === 1 &&
        sameId(r.character_ids[0], c.id),
    );
    if (!room) {
      room = await api("/api/rooms", "POST", {
        title: c.name,
        description: `A private conversation with ${c.name}.`,
        kind: "direct",
        character_ids: [c.id],
        turn_limit: 1,
      });
      await refreshData();
    }
    if ($("editor-dialog").open) closeDialog();
    await selectRoom(room.id);
  } catch (error) {
    toast(error.message);
  } finally {
    openingDirect.delete(String(c.id));
  }
}
function characterLibrary() {
  const host = dialog("Extraordinary company");
  const header = el("div", "library-header");
  header.append(
    el(
      "p",
      "",
      "Familiar faces. Entirely new voices.\nEvery character is yours to reimagine.",
    ),
    button("＋ New character", "button", () => characterEditor()),
  );
  host.append(header);
  const grid = el("div", "library-grid");
  for (const c of state.characters) {
    const card = el("article", "character-card");
    const top = el("div", "character-card-top");
    const identity = el("div");
    identity.append(el("h3", "", c.name), el("span", "handle", `@${c.handle}`));
    top.append(avatar(c), identity);
    const actions = el("div", "card-actions");
    actions.append(
      button("Edit character", "text-button", () => characterEditor(c)),
      button("1:1 conversation ↗", "text-button", () => openDirect(c)),
    );
    card.append(
      top,
      el("p", "", c.persona || "A character waiting for a story."),
      actions,
    );
    grid.append(card);
  }
  host.append(grid);
  if (!state.characters.length)
    host.append(
      el(
        "p",
        "field-help",
        "Your first character begins with a name and a little imagination.",
      ),
    );
}
function characterEditor(existing = null) {
  const host = dialog(
    existing ? `Reimagine ${existing.name}` : "Someone worth talking to",
  );
  const form = el("form", "editor-form");
  host.append(form);
  const epoch = state.dialogEpoch;
  const name = field("Character name", existing?.name || "");
  name.input.required = true;
  name.input.maxLength = 100;
  const handle = field(
    "Handle",
    existing?.handle || "",
    "text",
    "Character identifier. Letters, numbers and underscores.",
  );
  handle.input.pattern = "[A-Za-z0-9_]+";
  handle.input.maxLength = 60;
  handle.input.required = true;
  const persona = field(
    "Persona & character direction",
    existing?.persona || "",
    "textarea",
    "Go beyond a biography: temperament, motives, contradictions, relationships, speaking style, and what they would never do.",
  );
  persona.input.rows = 7;
  persona.input.required = true;
  persona.input.maxLength = 16000;
  persona.input.placeholder =
    "You are…\nYou want… but you are afraid of…\nYou speak with…\nStay in character. Respond to the others, not just the user.";
  form.append(row(name, handle), persona.wrap);
  let iconUrl = existing?.icon_url || "";
  let voiceUrl = existing?.voice_url || "";
  let previewObjectUrl = null;
  const icon = field(
    "Character icon",
    "",
    "file",
    "PNG, JPEG, GIF or WebP · up to 5 MB.",
  );
  icon.input.accept = "image/png,image/jpeg,image/gif,image/webp";
  const iconPreview = el("div", "upload-preview");
  const previewHost = el("div");
  previewHost.append(avatar(existing || { name: "New character" }, "large"));
  iconPreview.append(previewHost, icon.wrap);
  form.append(iconPreview);
  const removeIcon = button("Remove icon · use initials", "text-button", () => {
    iconUrl = "";
    icon.input.value = "";
    previewHost.replaceChildren(
      avatar({ name: name.input.value, color: color.input.value }, "large"),
    );
  });
  form.append(removeIcon);
  icon.input.addEventListener("change", () => {
    const file = icon.input.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      icon.input.value = "";
      toast("Choose an icon smaller than 5 MB.");
      return;
    }
    const url = URL.createObjectURL(file);
    const img = el("img", "avatar large");
    img.alt = "Selected character icon preview";
    img.src = url;
    img.addEventListener("load", () => URL.revokeObjectURL(url), {
      once: true,
    });
    img.addEventListener("error", () => URL.revokeObjectURL(url), {
      once: true,
    });
    previewHost.replaceChildren(img);
  });
  const voiceSection = el("section", "voice-section");
  voiceSection.append(
    el("h3", "", "Give the words a voice"),
    el(
      "p",
      "field-help",
      "No reference clip means the voice service uses its default voice—not a clone. Upload a voice you have permission to use to enable cloning.",
    ),
  );
  const reference = field(
    "Reference voice recording",
    "",
    "file",
    "WAV, MP3, OGG or FLAC · up to 25 MB. A clean short recording works best.",
  );
  reference.input.accept =
    ".wav,.mp3,.ogg,.flac,audio/wav,audio/mpeg,audio/ogg,audio/flac";
  const preview = el("audio", "voice-preview");
  preview.controls = true;
  preview.preload = "none";
  preview.setAttribute("aria-label", "Reference voice preview");
  preview.hidden = !safeMedia(voiceUrl);
  if (safeMedia(voiceUrl)) preview.src = voiceUrl;
  const transcript = field(
    "Exact reference transcript",
    existing?.voice_transcript || "",
    "textarea",
    "Required for cloning. Type exactly what is spoken in the reference recording.",
  );
  transcript.input.rows = 3;
  const voice = field(
    "Default voice",
    existing?.voice || "auto",
    "text",
    "Voice identifier supported by your local voice service; used only when no reference recording is set.",
  );
  const removeVoice = button(
    "Remove reference recording",
    "text-button",
    () => {
      voiceUrl = "";
      reference.input.value = "";
      preview.pause();
      preview.removeAttribute("src");
      preview.hidden = true;
      transcript.input.required = false;
    },
  );
  reference.input.addEventListener("change", () => {
    const file = reference.input.files[0];
    if (!file) return;
    if (file.size > 25 * 1024 * 1024) {
      reference.input.value = "";
      toast("Choose a reference recording smaller than 25 MB.");
      return;
    }
    if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = URL.createObjectURL(file);
    preview.src = previewObjectUrl;
    preview.hidden = false;
    transcript.input.required = true;
  });
  transcript.input.required = !!voiceUrl;
  voiceSection.append(
    reference.wrap,
    preview,
    removeVoice,
    transcript.wrap,
    voice.wrap,
  );
  form.append(voiceSection);
  const advanced = el("details");
  advanced.append(
    el("summary", "", "Advanced · model, connection & appearance"),
  );
  const advancedFields = el("div", "advanced-fields");
  advanced.append(advancedFields);
  const endpoint = field(
    "OpenAI-compatible model endpoint",
    existing?.endpoint ??
      state.services?.llm?.endpoint ??
      "http://127.0.0.1:8080/v1",
    "url",
  );
  endpoint.input.required = true;
  const model = field(
    "Model ID",
    existing?.model || "",
    "text",
    "Leave empty to discover the first available model.",
  );
  model.input.placeholder = state.services?.llm?.models?.[0] || "Auto-discover";
  const key = field(
    "API key",
    "",
    "password",
    existing?.has_api_key
      ? "A key is saved. Leave blank to keep it unchanged."
      : "Optional. Sent only to your local Ensemble server.",
  );
  key.input.autocomplete = "new-password";
  const clearKey = checkbox("Remove saved API key");
  clearKey.wrap.hidden = !existing?.has_api_key;
  const temperature = field(
    "Temperature",
    existing?.temperature ?? 0.85,
    "number",
  );
  temperature.input.min = 0;
  temperature.input.max = 2;
  temperature.input.step = 0.05;
  temperature.input.required = true;
  const tokens = field(
    "Maximum output tokens",
    existing?.max_tokens ?? 2048,
    "number",
    "Includes reasoning tokens on some local models. Raise this if a reply cannot finish.",
  );
  tokens.input.min = 1;
  tokens.input.max = 32768;
  tokens.input.required = true;
  const color = field(
    "Signature color",
    /^#[0-9a-f]{6}$/i.test(existing?.color || "") ? existing.color : "#94d7d0",
    "color",
  );
  advancedFields.append(
    endpoint.wrap,
    model.wrap,
    key.wrap,
    clearKey.wrap,
    row(temperature, tokens),
    color.wrap,
  );
  if (existing) {
    const testResult = el("p", "field-help");
    const test = button(
      "Test saved model connection",
      "button secondary",
      async () => {
        test.disabled = true;
        testResult.textContent = "Asking the saved model for a short response…";
        try {
          const result = await api(
            `/api/characters/${encodeURIComponent(existing.id)}/test`,
            "POST",
            {},
          );
          testResult.textContent = result.ok
            ? "Connected. The saved model responded successfully."
            : result.error || "The saved model did not respond.";
          testResult.className = result.ok ? "field-help" : "inline-error";
        } catch (error) {
          testResult.textContent = error.message;
          testResult.className = "inline-error";
        } finally {
          test.disabled = false;
        }
      },
    );
    advancedFields.append(
      test,
      el(
        "p",
        "field-help",
        "Connection test uses the saved character. Save changes first to test new settings.",
      ),
      testResult,
    );
  }
  form.append(advanced);
  const report = formError(form);
  const actions = el("div", "dialog-actions");
  if (existing)
    actions.append(
      button("Delete", "button danger", () =>
        confirmDialog(
          "Let this character go?",
          `Delete ${existing.name} from your library and every room? Past messages keep their original attribution.`,
          async () => {
            await api(
              `/api/characters/${encodeURIComponent(existing.id)}`,
              "DELETE",
            );
            await reloadAfterMutation();
            characterLibrary();
            toast("Character deleted.");
          },
          () => characterEditor(existing),
        ),
      ),
    );
  actions.append(button("Cancel", "button secondary", characterLibrary));
  const save = el(
    "button",
    "button",
    existing ? "Save character" : "Create character",
  );
  save.type = "submit";
  actions.append(save);
  form.append(actions);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (
      (voiceUrl || reference.input.files.length) &&
      !transcript.input.value.trim()
    ) {
      report(
        new Error(
          "Add the exact spoken transcript to use this reference voice.",
        ),
      );
      transcript.input.focus();
      return;
    }
    formBusy(form, true);
    try {
      const uploads = [];
      if (icon.input.files[0])
        uploads.push(
          upload("icon", icon.input.files[0]).then((url) => (iconUrl = url)),
        );
      if (reference.input.files[0])
        uploads.push(
          upload("voice", reference.input.files[0]).then(
            (url) => (voiceUrl = url),
          ),
        );
      await Promise.all(uploads);
      const payload = {
        name: name.input.value.trim(),
        handle: handle.input.value.trim(),
        persona: persona.input.value.trim(),
        provider: "openai_compatible",
        endpoint: endpoint.input.value.trim(),
        model: model.input.value.trim(),
        temperature: Number(temperature.input.value),
        max_tokens: Number(tokens.input.value),
        color: color.input.value,
        icon_url: iconUrl,
        voice_url: voiceUrl,
        voice_transcript: transcript.input.value.trim(),
        voice: voice.input.value.trim() || "auto",
      };
      if (key.input.value) payload.api_key = key.input.value;
      if (clearKey.input.checked) payload.clear_api_key = true;
      await api(
        existing
          ? `/api/characters/${encodeURIComponent(existing.id)}`
          : "/api/characters",
        existing ? "PUT" : "POST",
        payload,
      );
      if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
      await reloadAfterMutation();
      if (epoch === state.dialogEpoch) characterLibrary();
      toast(
        existing ? "Character updated." : "A new voice joins the ensemble.",
      );
    } catch (error) {
      report(error);
      formBusy(form, false);
    }
  });
  name.input.focus();
}
async function upload(kind, file) {
  const data = new FormData();
  data.append("file", file);
  const result = await api(`/api/uploads/${kind}`, "POST", data);
  if (!safeMedia(result.url))
    throw new Error("Upload returned an invalid media address.");
  return result.url;
}

async function loadServices() {
  try {
    state.services = await api("/api/services");
  } catch (error) {
    state.services = {
      llm: { ok: false, error: error.message },
      voice: { ok: false, error: error.message },
    };
  }
  for (const role of ["llm", "voice"]) {
    const service = state.services[role];
    const host = $(`${role}-status`);
    host.querySelector(".status-dot").className =
      `status-dot ${service?.ok ? "connected" : "disconnected"}`;
    host.querySelector("small").textContent = service?.ok
      ? "Connected"
      : "Offline";
    host.title = service?.error || service?.endpoint || "";
  }
}
function serviceDialog() {
  const host = dialog("Your local connections");
  for (const [role, title, variable] of [
    ["llm", "Language model", "LLM_BASE_URL"],
    ["voice", "Voice studio", "VOICE_BASE_URL"],
  ]) {
    const service = state.services?.[role];
    const section = el("section", "service-detail");
    const heading = el("h3");
    heading.append(
      el("span", `status-dot ${service?.ok ? "connected" : "disconnected"}`),
      document.createTextNode(
        `${title} · ${service?.ok ? "connected" : "offline"}`,
      ),
    );
    section.append(
      heading,
      el("code", "", service?.endpoint || "Endpoint unavailable"),
      el(
        "p",
        "",
        role === "llm"
          ? "Each character can use a different model endpoint. Open their Advanced settings to change it."
          : "Reference recordings and transcripts live on each character. Speech is synthesized on demand, without changing your conversation text.",
      ),
      el(
        "p",
        "",
        `The shared server address is configured with ${variable}. Change the environment variable and restart Ensemble to update it.`,
      ),
    );
    if (service?.models?.length)
      section.append(
        el("p", "", `Available models: ${service.models.join(", ")}`),
      );
    if (service?.error) section.append(el("p", "inline-error", service.error));
    host.append(section);
  }
  const actions = el("div", "dialog-actions");
  const recheck = button(
    "Recheck connections",
    "button secondary",
    async () => {
      const epoch = state.dialogEpoch;
      recheck.disabled = true;
      recheck.textContent = "Checking…";
      await loadServices();
      if (epoch === state.dialogEpoch && $("editor-dialog").open)
        serviceDialog();
    },
  );
  actions.append(recheck, button("Done", "button", closeDialog));
  host.append(actions);
}
function closeDrawers() {
  $("navigation").classList.remove("open");
  $("cast-panel").classList.remove("open");
  $("drawer-scrim").hidden = true;
}
function openDrawer(id) {
  closeDrawers();
  $(id).classList.add("open");
  $("drawer-scrim").hidden = false;
  $(id).querySelector("button, a")?.focus();
}
$("new-room").addEventListener("click", () => roomEditor());
$("home-link").addEventListener("click", (event) => {
  event.preventDefault();
  if (state.rooms.length) selectRoom(state.rooms[0].id);
  else selectRoom(null);
});
$("characters-button").addEventListener("click", characterLibrary);
for (const id of ["edit-room", "edit-scene", "manage-cast"])
  $(id).addEventListener("click", () => roomEditor(selectedRoom()));
$("delete-room").addEventListener("click", deleteRoom);
for (const id of ["service-settings", "llm-status", "voice-status"])
  $(id).addEventListener("click", serviceDialog);
$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  launchRun();
});
$("message-input").addEventListener("input", () => {
  roomState(state.selected).draft = $("message-input").value;
  renderControls();
});
$("message-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!$("send-button").disabled) $("composer").requestSubmit();
  }
});
$("target-select").addEventListener(
  "change",
  () => (roomState(state.selected).target = $("target-select").value),
);
$("turn-limit").addEventListener(
  "change",
  () => (roomState(state.selected).budget = $("turn-limit").value),
);
$("continue-button").addEventListener("click", () => launchRun(true));
$("stop-button").addEventListener("click", stopRun);
$("auto-play").checked = preference("ensemble.autoplay") === "true";
$("auto-play").addEventListener("change", () => {
  savePreference("ensemble.autoplay", $("auto-play").checked);
  if (!$("auto-play").checked) state.queue = [];
});
$("pause-audio").addEventListener("click", () => {
  if (state.playing) playMessage(state.playing, true);
});
$("close-audio").addEventListener("click", stopAudio);
$("close-dialog").addEventListener("click", closeDialog);
$("editor-dialog").addEventListener("cancel", (event) => {
  event.preventDefault();
  closeDialog();
});
$("editor-dialog").addEventListener("close", () => {
  for (const audio of $("dialog-body").querySelectorAll("audio")) audio.pause();
});
$("open-navigation").addEventListener("click", () => openDrawer("navigation"));
$("open-cast").addEventListener("click", () => openDrawer("cast-panel"));
$("close-cast").addEventListener("click", closeDrawers);
$("drawer-scrim").addEventListener("click", closeDrawers);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("editor-dialog").open) closeDrawers();
});
window.addEventListener("online", async () => {
  await loadServices();
  if (state.selected !== null)
    refreshMessages(state.selected).catch((error) => showError(error));
});
async function initialize() {
  renderRoom();
  loadServices();
  try {
    await refreshData();
    const saved = preference("ensemble.room");
    const room = state.rooms.find((r) => sameId(r.id, saved)) || state.rooms[0];
    await selectRoom(room?.id ?? null);
  } catch (error) {
    showError(error);
    const retry = button("Retry connection", "button secondary", () => {
      retry.remove();
      initialize();
    });
    $("empty-state").append(retry);
  }
}
initialize();
