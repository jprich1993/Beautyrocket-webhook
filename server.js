const express = require("express");
const { Pool } = require("pg");

const app = express();
app.use(express.json());

const VERIFY_TOKEN = process.env.VERIFY_TOKEN;
const INSTAGRAM_ACCESS_TOKEN = process.env.INSTAGRAM_ACCESS_TOKEN;
const DATABASE_URL = process.env.DATABASE_URL;

// Connect to Render Postgres
const pool = new Pool({
  connectionString: DATABASE_URL,
  ssl: {
    rejectUnauthorized: false,
  },
});

// Create the database table if it doesn't exist
async function initializeDatabase() {
  try {
    await pool.query(`
      CREATE TABLE IF NOT EXISTS greeted_users (
        instagram_user_id TEXT PRIMARY KEY,
        greeted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);

    console.log("Database initialized successfully.");
  } catch (error) {
    console.error("Database initialization error:", error);
  }
}

// Check whether this Instagram user has already received the greeting
async function hasBeenGreeted(instagramUserId) {
  const result = await pool.query(
    `
    SELECT instagram_user_id
    FROM greeted_users
    WHERE instagram_user_id = $1
    `,
    [instagramUserId]
  );

  return result.rows.length > 0;
}

// Remember that this Instagram user received the greeting
async function markAsGreeted(instagramUserId) {
  await pool.query(
    `
    INSERT INTO greeted_users (instagram_user_id)
    VALUES ($1)
    ON CONFLICT (instagram_user_id) DO NOTHING
    `,
    [instagramUserId]
  );
}

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

    console.log(
      "Instagram API response:",
      JSON.stringify(data, null, 2)
    );

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
          `Incoming message from ${senderId}: ${
            messageText || "[non-text message]"
          }`
        );

        // Check the database
        const alreadyGreeted = await hasBeenGreeted(senderId);

        // If this person already received the greeting,
        // Beautyrocket stays completely silent.
        if (alreadyGreeted) {
          console.log(
            "Conversation already greeted; waiting for Sheila."
          );
          continue;
        }

        // Save the user BEFORE sending the greeting.
        // This prevents duplicate greetings if Meta sends
        // the webhook more than once.
        await markAsGreeted(senderId);

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

app.listen(PORT, async () => {
  console.log(
    `Beautyrocket webhook listening on port ${PORT}`
  );

  await initializeDatabase();
});
