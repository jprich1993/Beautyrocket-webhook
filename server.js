const express = require("express");

const app = express();
app.use(express.json());

const VERIFY_TOKEN = process.env.VERIFY_TOKEN;
const INSTAGRAM_ACCESS_TOKEN = process.env.INSTAGRAM_ACCESS_TOKEN;

// Keeps track of people who already received the automatic greeting.
// Note: this resets if Render restarts/redeploys the service.
const greetedUsers = new Set();

const initialGreeting =
  "Hi! 👋💕 I’m glad you’re enjoying my content! Let’s be online friends. Feel free to follow me, ask me questions, or send me suggestions for what I should film next! 🎥✨";

// Send a message through Instagram
async function sendInstagramMessage(recipientId, text) {
  try {
    const response = await fetch(
      "https://graph.instagram.com/v24.0/me/messages",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${INSTAGRAM_ACCESS_TOKEN}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          recipient: {
            id: recipientId,
          },
          message: {
            text: text,
          },
        }),
      }
    );

    const data = await response.json();

    console.log("Instagram API response:", JSON.stringify(data, null, 2));

    if (!response.ok) {
      console.error("Instagram API error:", data);
    }
  } catch (error) {
    console.error("Error sending Instagram message:", error);
  }
}

// Webhook verification
app.get("/webhook", (req, res) => {
  const mode = req.query["hub.mode"];
  const token = req.query["hub.verify_token"];
  const challenge = req.query["hub.challenge"];

  if (mode === "subscribe" && token === VERIFY_TOKEN) {
    console.log("Webhook verified successfully.");
    return res.status(200).send(challenge);
  }

  console.log("Webhook verification failed.");
  return res.sendStatus(403);
});

// Receive Instagram messages
app.post("/webhook", async (req, res) => {
  console.log(
    "Incoming webhook:",
    JSON.stringify(req.body, null, 2)
  );

  // Tell Meta we received the webhook
  res.sendStatus(200);

  try {
    if (req.body.object !== "instagram") {
      return;
    }

    for (const entry of req.body.entry || []) {
      for (const messagingEvent of entry.messaging || []) {

        // Ignore our own outgoing messages
        if (messagingEvent.message?.is_echo) {
          console.log("Ignoring own outgoing message.");
          continue;
        }

        const senderId = messagingEvent.sender?.id;
        const messageText = messagingEvent.message?.text;

        if (!senderId) {
          console.log("No sender ID found.");
          continue;
        }

        console.log(
          `Incoming message from ${senderId}: ${messageText || "[non-text message]"}`
        );

        // If this person already received the greeting,
        // Beautyrocket stays completely silent.
        if (greetedUsers.has(senderId)) {
          console.log(
            `Conversation already greeted; waiting for Sheila.`
          );
          continue;
        }

        // Mark this person as greeted BEFORE sending the message
        // so the bot will not respond again.
        greetedUsers.add(senderId);

        console.log(
          `Sending initial greeting to ${senderId}.`
        );

        await sendInstagramMessage(
          senderId,
          initialGreeting
        );
      }
    }
  } catch (error) {
    console.error("Webhook processing error:", error);
  }
});

// Health check
app.get("/", (req, res) => {
  res.send("Beautyrocket webhook is running.");
});

// Start server
const PORT = process.env.PORT || 10000;

app.listen(PORT, () => {
  console.log(`Beautyrocket webhook listening on port ${PORT}`);
});
