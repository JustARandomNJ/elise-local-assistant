(() => {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const api = (path) => `${path}?token=${encodeURIComponent(token)}`;
  const message = document.getElementById("message");
  const fallback = document.getElementById("play-fallback");
  let player = null;
  let playerReady = false;
  let loadedVideoId = null;
  let lastRevision = -1;

  const report = (status) => {
    if (!loadedVideoId) return;
    fetch(api("/api/report"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status, video_id: loadedVideoId }) }).catch(() => {});
  };
  const showFallback = () => { fallback.hidden = false; message.textContent = "Autoplay was blocked. Click to play."; report("autoplay_blocked"); };
  window.onYouTubeIframeAPIReady = () => {
    player = new YT.Player("player", {
      playerVars: { autoplay: 1, playsinline: 1, rel: 0 },
      events: {
        onReady: () => { playerReady = true; },
        onStateChange: (event) => {
          if (event.data === YT.PlayerState.PLAYING) { fallback.hidden = true; message.textContent = ""; report("playing"); }
          if (event.data === YT.PlayerState.PAUSED) report("paused");
          if (event.data === YT.PlayerState.ENDED) report("ended");
        },
        onError: () => { message.textContent = "The YouTube player reported an error."; report("player_error"); }
      }
    });
  };
  const script = document.createElement("script"); script.src = "https://www.youtube.com/iframe_api"; document.head.appendChild(script);
  fallback.addEventListener("click", () => { if (playerReady) { player.playVideo(); fallback.hidden = true; } });
  const apply = (state) => {
    if (state.revision === lastRevision) return;
    const media = state.media;
    if (state.action === "load" && media && media.kind === "youtube") {
      if (!playerReady) return;
      loadedVideoId = media.video_id;
      lastRevision = state.revision;
      message.textContent = media.title;
      fallback.hidden = true;
      report("player_ready");
      player.loadVideoById(media.video_id);
      setTimeout(() => { if (player.getPlayerState() !== YT.PlayerState.PLAYING) showFallback(); }, 2500);
    } else if (state.action === "pause" && playerReady) { lastRevision = state.revision; player.pauseVideo(); }
    else if (state.action === "resume" && playerReady) { lastRevision = state.revision; player.playVideo(); setTimeout(() => { if (player.getPlayerState() !== YT.PlayerState.PLAYING) showFallback(); }, 1500); }
    else if (state.action === "close") { lastRevision = state.revision; window.close(); }
  };
  setInterval(() => fetch(api("/api/state"), { cache: "no-store" }).then(r => r.ok ? r.json() : null).then(state => { if (state) apply(state); }).catch(() => {}), 400);
})();
