const express = require("express");

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 10000;
const VERIFY_TOKEN = process.env.VERIFY_TOKEN;
const INSTAGRAM_ACCESS_TOKEN = process.env.INSTAGRAM_ACCESS_TOKEN;

// Keep track of conversations that have been handed off to Sheila
const handedOffUsers = new Set();


// --------------------------------------------------
// META WEBHOOK VERIFICATION
// --------------------------------------------------

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


// --------------------------------------------------
// SEND INSTAGRAM DIRECT MESSAGE
// --------------------------------------------------

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


// --------------------------------------------------
// BEAUTY MENU
// --------------------------------------------------

function beautyMenu() {
  return (
    "Absolutely! 💕 What can I help you with?\n\n" +
    "💇🏽‍♀️ Hair\n" +
    "💅🏽 Nails\n" +
    "💄 Makeup\n" +
    "✨ Skincare\n" +
    "👁️ Lashes & Brows\n" +
    "💕 Something personal\n" +
    "🌸 Other beauty questions"
  );
}


// --------------------------------------------------
// RESPONSE LOGIC
// --------------------------------------------------

function createReply(incomingText) {

  const text = incomingText.toLowerCase().trim();


  // -----------------------------------------------
  // GREETINGS
  // -----------------------------------------------

  const greetings = [
    "hi",
    "hello",
    "hey",
    "good morning",
    "good afternoon",
    "good evening",
    "hola"
  ];

  if (
    greetings.some(
      greeting =>
        text === greeting ||
        text.startsWith(greeting + " ")
    )
  ) {

    return {
      reply:
        "Hi! 👋💕 I’m glad you’re enjoying my content! Let’s be online friends. Feel free to follow me, ask me questions, or send me suggestions for what I should film next! 🎥✨",

      handoff: false
    };
  }


  // -----------------------------------------------
  // BEAUTY CATEGORY HANDOFF
  // -----------------------------------------------

  const handoffKeywords = [

    "hair",

    "nails",

    "makeup",

    "skincare",
    "skin care",

    "lashes",
    "lash",

    "brows",
    "eyebrows",

    "something personal",

    "other beauty questions",
    "other beauty"
  ];


  if (
    handoffKeywords.some(keyword =>
      text.includes(keyword)
    )
  ) {

    return {
      reply:
        "Absolutely 😊 Sheila will take it from here and get back to you personally.",

      handoff: true
    };
  }


  // -----------------------------------------------
  // GENERAL BEAUTY QUESTIONS
  // -----------------------------------------------

  const beautyKeywords = [
    "beauty",
    "cosmetic",
    "cosmetics",
    "glam"
  ];


  if (
    beautyKeywords.some(keyword =>
      text.includes(keyword)
    )
  ) {

    return {
      reply: beautyMenu(),
      handoff: false
    };
  }


  // -----------------------------------------------
  // GENERAL QUESTIONS
  // -----------------------------------------------

  return {
    reply:
      "Hi! 👋💕 Thanks for reaching out! Tell me a little about what you're looking for and I'll do my best to help.",

    handoff: false
  };
}


// --------------------------------------------------
// RECEIVE INSTAGRAM WEBHOOK EVENTS
// --------------------------------------------------

app.post("/webhook", async (req, res) => {

  console.log("Instagram webhook event received:");

  console.log(
    JSON.stringify(req.body, null, 2)
  );


  try {

    if (req.body.object === "instagram") {

      for (const entry of req.body.entry || []) {

        for (
          const messagingEvent of
          entry.messaging || []
        ) {


          // -----------------------------------------
          // IGNORE OUR OWN OUTGOING MESSAGES
          // -----------------------------------------

          if (
            messagingEvent.message?.is_echo
          ) {

            console.log(
              "Ignoring own outgoing message."
            );

            continue;
          }


          // -----------------------------------------
          // ONLY PROCESS INCOMING MESSAGES
          // -----------------------------------------

          if (
            messagingEvent.message &&
            messagingEvent.sender
          ) {

            const senderId =
              messagingEvent.sender.id;

            const incomingText =
              messagingEvent.message.text || "";


            console.log(
              `Incoming Instagram message: ${incomingText}`
            );


            // ---------------------------------------
            // CHECK IF SHEILA HAS TAKEN OVER
            // ---------------------------------------

            if (
              handedOffUsers.has(senderId)
            ) {

              console.log(
                `Conversation already handed off to Sheila for ${senderId}.`
              );

              continue;
            }


            // ---------------------------------------
            // DETERMINE RESPONSE
            // ---------------------------------------

            const response =
              createReply(incomingText);


            // ---------------------------------------
            // SEND RESPONSE
            // ---------------------------------------

            await sendInstagramMessage(
              senderId,
              response.reply
            );


            // ---------------------------------------
            // HAND OFF TO SHEILA
            // ---------------------------------------

            if (response.handoff) {

              handedOffUsers.add(senderId);

              console.log(
                `Conversation handed off to Sheila for ${senderId}.`
              );
            }
          }
        }
      }
    }

  } catch (error) {

    console.error(
      "Error processing Instagram webhook:"
    );

    console.error(error);
  }


  // Always acknowledge Meta's webhook
  res.sendStatus(200);
});


// --------------------------------------------------
// HEALTH CHECK
// --------------------------------------------------

app.get("/", (req, res) => {

  res
    .status(200)
    .send(
      "Beautyrocket webhook is running."
    );
});


// --------------------------------------------------
// START SERVER
// --------------------------------------------------

app.listen(
  PORT,
  "0.0.0.0",
  () => {

    console.log(
      `Beautyrocket webhook listening on port ${PORT}`
    );
  }
);
