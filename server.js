const express = require("express");

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 10000;
const VERIFY_TOKEN = process.env.VERIFY_TOKEN;
const INSTAGRAM_ACCESS_TOKEN = process.env.INSTAGRAM_ACCESS_TOKEN;

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

// Send an Instagram Direct Message
async function sendInstagramMessage(recipientId, text) {
  try {
    const response = await fetch(
      "https://graph.instagram.com/v24.0/me/messages",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${INSTAGRAM_ACCESS_TOKEN}`
        },
        body: JSON.stringify({
          recipient: {
            id: recipientId
          },
          message: {
            text: text
          }
        })
      }
    );

    const data = await response.json();

    console.log("Instagram send response:");
    console.log(JSON.stringify(data, null, 2));

    if (!response.ok) {
      console.error("Instagram message failed.");
    }

    return data;
  } catch (error) {
    console.error("Error sending Instagram message:");
    console.error(error);
  }
}

// Receive Instagram webhook events
app.post("/webhook", async (req, res) => {
  console.log("Instagram webhook event received:");
  console.log(JSON.stringify(req.body, null, 2));

  try {
    if (req.body.object === "instagram") {
      for (const entry of req.body.entry || []) {
        for (const messagingEvent of entry.messaging || []) {

          // Ignore messages sent by our own Instagram account
          if (messagingEvent.message?.is_echo) {
            console.log("Ignoring own outgoing message.");
            continue;
          }

          // Only respond to incoming messages
          if (messagingEvent.message && messagingEvent.sender) {
            const senderId = messagingEvent.sender.id;
            const incomingText = messagingEvent.message.text || "";

            console.log(`Incoming Instagram message: ${incomingText}`);

            await sendInstagramMessage(
              senderId,
              "Hi! 👋 Thanks for reaching out! I got your message and will get back to you shortly. 😊"
            );
          }
        }
      }
    }
  } catch (error) {
    console.error("Error processing Instagram webhook:");
    console.error(error);
  }

  // Always acknowledge Meta's webhook
  res.sendStatus(200);
});

// Health check
app.get("/", (req, res) => {
  res.status(200).send("Beautyrocket webhook is running.");
});

app.listen(PORT, "0.0.0.0", () => {
  console.log(`Beautyrocket webhook listening on port ${PORT}`);
});
