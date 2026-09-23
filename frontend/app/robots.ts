import type { MetadataRoute } from "next";

/**
 * A private desk has nothing for a crawler. Well-behaved bots stop here; the
 * rest meet the sign-in redirect, the rate limit and the lockout.
 */
export default function robots(): MetadataRoute.Robots {
  return { rules: { userAgent: "*", disallow: "/" } };
}
