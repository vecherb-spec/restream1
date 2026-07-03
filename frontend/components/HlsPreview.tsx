"use client";

import { useEffect, useMemo, useRef } from "react";
import { HLS_BASE_URL } from "@/lib/api";

type HlsPreviewProps = {
  streamKey: string;
};

type HlsConstructor = new (options?: Record<string, unknown>) => {
  loadSource: (source: string) => void;
  attachMedia: (video: HTMLVideoElement) => void;
  on: (event: string, callback: (event: unknown, data: { fatal?: boolean }) => void) => void;
  destroy: () => void;
};

type HlsGlobal = HlsConstructor & {
  isSupported: () => boolean;
  Events: {
    MANIFEST_PARSED: string;
    ERROR: string;
  };
};

export function HlsPreview({ streamKey }: HlsPreviewProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const sourceUrl = useMemo(() => `${HLS_BASE_URL}/${streamKey}.m3u8`, [streamKey]);

  function jumpToLive() {
    const video = videoRef.current;
    if (!video || video.seekable.length === 0) {
      video?.play().catch(() => undefined);
      return;
    }

    const liveEdge = video.seekable.end(video.seekable.length - 1);
    video.currentTime = Math.max(0, liveEdge - 0.5);
    video.play().catch(() => undefined);
  }

  useEffect(() => {
    const video = videoRef.current;
    if (!video) {
      return;
    }

    let hls: InstanceType<HlsConstructor> | null = null;
    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = sourceUrl;
      video.play().catch(() => undefined);
      return;
    }

    const script = document.createElement("script");
    script.src = "https://cdn.jsdelivr.net/npm/hls.js@latest";
    script.async = true;
    script.onload = () => {
      const Hls = (window as unknown as { Hls?: HlsGlobal }).Hls;
      if (!Hls?.isSupported()) {
        return;
      }

      hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 3 });
      hls.loadSource(sourceUrl);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        video.play().catch(() => undefined);
      });
    };
    document.body.appendChild(script);

    return () => {
      hls?.destroy();
      script.remove();
    };
  }, [sourceUrl]);

  return (
    <div className="preview-shell">
      <video ref={videoRef} className="preview" controls muted autoPlay playsInline />
      <button className="player-live-button" onClick={jumpToLive}>
        Live
      </button>
    </div>
  );
}
