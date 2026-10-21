export const DEFAULT_CATEGORIES = [
  {
    name: "Food & Dining",
    subcategories: ["Restaurants", "Groceries", "Zomato/Swiggy", "Coffee/Snacks"],
  },
  { name: "Transport", subcategories: ["Fuel", "Uber/Ola", "Metro/Bus", "Parking"] },
  { name: "Shopping", subcategories: ["Clothes", "Electronics", "Amazon/Flipkart", "Household"] },
  {
    name: "Bills & Utilities",
    subcategories: ["Electricity", "Water", "Internet", "Phone", "Gas"],
  },
  { name: "Rent & Housing", subcategories: ["Rent", "Maintenance", "Repairs"] },
  { name: "Health", subcategories: ["Medicine", "Doctor", "Insurance", "Gym"] },
  { name: "Entertainment", subcategories: ["Movies", "Subscriptions", "Gaming", "Events"] },
  { name: "Travel", subcategories: ["Flights", "Hotels", "Local Transport", "Activities"] },
  { name: "Education", subcategories: ["Courses", "Books", "Certifications"] },
  { name: "Personal", subcategories: ["Grooming", "Clothing", "Gifts"] },
  { name: "EMI & Loans", subcategories: ["Home Loan", "Car Loan", "Personal Loan", "Credit Card"] },
  { name: "Investments", subcategories: ["SIP", "Stocks", "FD", "Gold", "NPS"] },
  { name: "Miscellaneous", subcategories: ["ATM Withdrawal", "Donation", "Other"] },
];

export const CATEGORIES: Record<string, string[]> = {};
DEFAULT_CATEGORIES.forEach((c) => {
  CATEGORIES[c.name] = c.subcategories;
});
