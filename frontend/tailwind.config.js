/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      animation: {
        "blink-red": "blink-red 1s step-end infinite",
        "blink-green": "blink-green 1s ease-in-out infinite",
      },
      keyframes: {
        "blink-red": {
          "0%, 100%": { opacity: "1", boxShadow: "0 0 0 2px #ef4444" },
          "50%": { opacity: "0.65", boxShadow: "0 0 0 6px #f87171" },
        },
        "blink-green": {
          "0%, 100%": { opacity: "1", boxShadow: "0 0 0 2px #22c55e" },
          "50%": { opacity: "0.88", boxShadow: "0 0 0 8px #4ade80" },
        },
      },
    },
  },
  plugins: [],
};
