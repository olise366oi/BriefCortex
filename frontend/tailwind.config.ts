import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        grape: "#7C5CFF",
        mint: "#34D3A0",
        butter: "#FFCE5C",
        coral: "#FF7A6B",
        sky: "#5BC3FF",
        ink: "#2B2350",
      },
      fontFamily: {
        display: ['"Baloo 2"', "cursive", "sans-serif"],
        body: ["Inter", "sans-serif"],
        mono: ['"JetBrains Mono"', "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
