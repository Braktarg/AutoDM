import { useEffect, useState } from "react";
import { apiUrl } from "@/lib/api";
import { getToken } from "@/lib/auth";

type Props = {
  campaignId: string;
  hasCover: boolean;
  fallbackSrc: string;
  alt: string;
  className?: string;
  width?: number;
  height?: number;
};

export function CampaignCoverImage({
  campaignId,
  hasCover,
  fallbackSrc,
  alt,
  className,
  width,
  height,
}: Props) {
  const [src, setSrc] = useState<string>(fallbackSrc);

  useEffect(() => {
    if (!hasCover) {
      setSrc(fallbackSrc);
      return;
    }
    let blobUrl: string | null = null;
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(apiUrl(`/api/campaigns/${campaignId}/cover`), {
          headers: { Authorization: `Bearer ${getToken()}` },
        });
        if (!r.ok || cancelled) {
          if (!cancelled) setSrc(fallbackSrc);
          return;
        }
        const blob = await r.blob();
        blobUrl = URL.createObjectURL(blob);
        if (!cancelled) setSrc(blobUrl);
      } catch {
        if (!cancelled) setSrc(fallbackSrc);
      }
    })();
    return () => {
      cancelled = true;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [campaignId, hasCover, fallbackSrc]);

  return <img src={src} alt={alt} className={className} width={width} height={height} loading="lazy" />;
}
