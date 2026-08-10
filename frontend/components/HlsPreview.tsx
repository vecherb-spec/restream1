"use client";

import Hls from "hls.js";
import { useEffect, useMemo, useRef } from "react";
import { HLS_BASE_URL } from "@/lib/api";

type HlsPreviewProps = {
  streamKey: string;
};

export function HlsPreview({ streamKey }: HlsPreviewProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const sourceUrl = useMemo(() => `${HLS_BASE_URL}/${streamKey}.m3u8`, [streamKey]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) {
      return;
    }

    let hls: Hls | null = null;
    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = sourceUrl;
      video.play().catch(() => undefined);
      return () => {
        video.removeAttribute("src");
        video.load();
      };
    }

    if (!Hls.isSupported()) {
      return;
    }

    hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 3 });
    hls.loadSource(sourceUrl);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      video.play().catch(() => undefined);
    });

    return () => {
      hls?.destroy();
      video.removeAttribute("src");
      video.load();
    };
  }, [sourceUrl]);

  return (
    <div className="preview-shell">
      <video ref={videoRef} className="preview" controls muted autoPlay playsInline />
    </div>
  );
}
