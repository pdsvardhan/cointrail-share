export function formatIndianNumber(num: number): string {
  const numStr = Math.abs(num).toFixed(2);
  const [intPart, decPart] = numStr.split(".");
  let formatted = "";
  let count = 0;
  for (let i = intPart.length - 1; i >= 0; i--) {
    if (count === 3 || (count > 3 && (count - 3) % 2 === 0)) formatted = "," + formatted;
    formatted = intPart[i] + formatted;
    count++;
  }
  return `${num < 0 ? "-" : ""}₹${formatted}.${decPart}`;
}

export function formatCompact(amount: number): string {
  const abs = Math.abs(amount);
  const sign = amount < 0 ? "-" : "";
  if (abs >= 10000000) return `${sign}₹${(abs / 10000000).toFixed(2)} Cr`;
  if (abs >= 100000) return `${sign}₹${(abs / 100000).toFixed(2)} L`;
  return formatIndianNumber(amount);
}

export function autoFormatCurrency(amount: number): string {
  return formatIndianNumber(amount);
}
