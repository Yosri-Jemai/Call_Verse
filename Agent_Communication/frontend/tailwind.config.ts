import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#172420",
        forest: "#0d4c3b",
        mint: "#c5f1d8",
        paper: "#f5f6f2",
      },
      boxShadow: {
        panel: "0 18px 50px rgba(27, 46, 37, 0.07)",
      },
    },
  },
  plugins: [],
};

export default config;
