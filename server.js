const express = require("express");

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 10000;
const VERIFY_TOKEN = process.env.VERIFY_TOKEN;

// Meta webhook verification
app.get("/webhook", (req, res) => {
  const mode = req.query["hub.mode"];
  const token = req.query["hub.verify_token"];
  const challenge = req.query["hub.challenge"];

  if (mode === "subscribe" && token === VERIFY_TOKEN) {
    console.log("Webhook verified by Meta.");
    return res.status(200).send(challenge);
  }

  return res.sendStatus(403);
});

// Receive Instagram webhook events
app.post("/webhook", (req, res) => {
  console.log("Instagram webhook event received:");
  console.log(JSON.stringify(req.body, null, 2));

  res.sendStatus(200);
});

// Health check
app.get("/", (req, res) => {
  res.status(200).send("Beautyrocket webhook is running.");
});

app.listen(PORT, "0.0.0.0", () => {
  console.log(`Beautyrocket webhook listening on port ${PORT}`);
});
