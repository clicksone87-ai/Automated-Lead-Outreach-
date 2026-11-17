/** Static export: every preview is plain files served by Caddy (no Node server per preview). */
const nextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
  reactStrictMode: true,
  eslint: { ignoreDuringBuilds: true },
  // The template is type-checked in the repo; preview builds skip it (the slowest build step
  // on a small machine). Content is plain JSON validated by the builder.
  typescript: { ignoreBuildErrors: true },
};

export default nextConfig;
