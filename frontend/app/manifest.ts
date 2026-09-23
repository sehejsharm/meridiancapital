import type { MetadataRoute } from "next";

/**
 * What Android and desktop Chrome read to install the desk as an app. iOS
 * ignores most of this and reads the apple-* tags from the layout instead.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "Meridian Capital",
    short_name: "Meridian",
    description: "Trading desk",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#0a0a0a",
    theme_color: "#0a0a0a",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/maskable-192.png", sizes: "192x192", type: "image/png", purpose: "maskable" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
