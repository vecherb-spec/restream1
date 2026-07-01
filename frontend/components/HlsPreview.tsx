"use client";

import { useEffect, useMemo, useRef, useState } from "react";
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
  const [message, setMessage] = useState("Если эфир запущен, превью появится через 10-30 секунд.");
  const sourceUrl = useMemo(() => `${HLS_BASE_URL}/${streamKey}.m3u8`, [streamKey]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) {
      return;
    }

    let hls: InstanceType<HlsConstructor> | null = null;
    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = sourceUrl;
      setMessage("Превью подключено. Нажмите Play.");
      return;
    }

    const script = document.createElement("script");
    script.src = "https://cdn.jsdelivr.net/npm/hls.js@latest";
    script.async = true;
    script.onload = () => {
      const Hls = (window as unknown as { Hls?: HlsGlobal }).Hls;
      if (!Hls?.isSupported()) {
        setMessage("Этот браузер не поддерживает HLS.");
        return;
      }

      hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 3 });
      hls.loadSource(sourceUrl);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        setMessage("Превью подключено. Нажмите Play.");
      });
      hls.on(Hls.Events.ERROR, (_event: unknown, data: { fatal?: boolean }) => {
        if (data?.fatal) {
          setMessage("Пока нет HLS-потока или он недоступен.");
        }
      });
    };
    document.body.appendChild(script);

    return () => {
      hls?.destroy();
      script.remove();
    };
  }, [sourceUrl]);

  return (
    <div>
      <video ref={videoRef} className="preview" controls muted playsInline />
      <p className="muted">{message}</p>
      <p className="muted">HLS: {sourceUrl}</p>
    </div>
  );
}
