import concurrent.futures as cf
import glob
import io
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import List, Literal

import gradio as gr

from loguru import logger
from openai import OpenAI
from promptic import llm
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader
from tenacity import retry, retry_if_exception_type

from functools import wraps

import re

# Enable LiteLLM debug mode for troubleshooting
try:
    import litellm
    litellm.set_verbose = True
except ImportError:
    pass

def read_readme():
    readme_path = Path("README.md")
    if readme_path.exists():
        with open(readme_path, "r") as file:
            content = file.read()
            # Use regex to remove metadata enclosed in -- ... --
            content = re.sub(r'--.*?--', '', content, flags=re.DOTALL)
            return content
    else:
        return "README.md not found. Please check the repository for more information."
        
# Define multiple sets of instruction templates
INSTRUCTION_TEMPLATES = {

################# PODCAST ##################
    "podcast": {
        "intro": """Your task is to take the input text provided and turn it into an lively, engaging, informative podcast dialogue, in the style of NPR. Do not use or make up names. The input text may be messy or unstructured, as it could come from a variety of sources like PDFs or web pages. 

Don't worry about the formatting issues or any irrelevant information; your goal is to extract the key points, identify definitions, and interesting facts that could be discussed in a podcast. 

Define all terms used carefully for a broad audience of listeners.
""",
        "text_instructions": "First, carefully read through the input text and identify the main topics, key points, and any interesting facts or anecdotes. Think about how you could present this information in a fun, engaging way that would be suitable for a high quality presentation.",
        "scratch_pad": """Brainstorm creative ways to discuss the main topics and key points you identified in the input text. Consider using analogies, examples, storytelling techniques, or hypothetical scenarios to make the content more relatable and engaging for listeners.

Keep in mind that your podcast should be accessible to a general audience, so avoid using too much jargon or assuming prior knowledge of the topic. If necessary, think of ways to briefly explain any complex concepts in simple terms.

Use your imagination to fill in any gaps in the input text or to come up with thought-provoking questions that could be explored in the podcast. The goal is to create an informative and entertaining dialogue, so feel free to be creative in your approach.

Define all terms used clearly and spend effort to explain the background.

Write your brainstorming ideas and a rough outline for the podcast dialogue here. Be sure to note the key insights and takeaways you want to reiterate at the end.

Make sure to make it fun and exciting. 
""",
        "prelude": """Now that you have brainstormed ideas and created a rough outline, it's time to write the actual podcast dialogue. Aim for a natural, conversational flow between the host and any guest speakers. Incorporate the best ideas from your brainstorming session and make sure to explain any complex topics in an easy-to-understand way.
""",
        "dialog": """Write a very long, engaging, informative podcast dialogue here, based on the key points and creative ideas you came up with during the brainstorming session. Use a conversational tone and include any necessary context or explanations to make the content accessible to a general audience. 

Never use made-up names for the hosts and guests, but make it an engaging and immersive experience for listeners. Do not include any bracketed placeholders like [Host] or [Guest]. Design your output to be read aloud -- it will be directly converted into audio.

Make the dialogue as long and detailed as possible, while still staying on topic and maintaining an engaging flow. Aim to use your full output capacity to create the longest podcast episode you can, while still communicating the key information from the input text in an entertaining way.

At the end of the dialogue, have the host and guest speakers naturally summarize the main insights and takeaways from their discussion. This should flow organically from the conversation, reiterating the key points in a casual, conversational manner. Avoid making it sound like an obvious recap - the goal is to reinforce the central ideas one last time before signing off. 

The podcast should have around 20000 words.
""",
    },
    
################# DEEP DATA ANALYSIS ##################
    "deep research analysis": {
    # 1) High‑level task description 
    "intro": """You are a senior analyst who conducts deep research.
    
Your job is to turn the raw materials supplied below (PDF text, markdown, tables, figures or loose CSV/TXT files) into a **deep research report** that humans can read.

The finished report must contain, in this exact order:

1. **Metadata block** – start with the title/s, authors, publication years (as they are available). If not available, start by describing the types of raw materials you analyzed.  
2. **Data extraction** – careful extraction of key data and quantitative information, presented as a carefully crafted narrative. For example, discuss the domain, industry, area of science. Define. all terms. 
3. **Key insights** – interpretation of the results.  This must be comprehensive and include your thoughts and interpretation, and context. 
4. **Examples** – pick a few examples to illustrate the key concepts (one or more). Use strong storytelling to show depth while making it broadly understandable.  
5. **Strengths** – strengths of the results or data, paper or information in the raw materials. 
6. **Weaknesses** – assess weaknesses of the document, paper or data. 
7. **Relating to other fields** – relate the raw materials to other fields, historical results, contemperary work, or other significant concepts. Discuss the significance.
8. **Open questions / action items** – what further analysis or experiments would you recommend?

Keep the narrative clear and concise, suitable for a technically literate audience, with depth.  Do **not** reveal chain‑of‑thought; only present the final reasoning.""",

    # 2) How to read / transform the source material
    "text_instructions": """Carefully scan the input text for any data, insights, and so on.  
If tables are broken across lines, reconstruct them logically to extract key insights.  

Translate uncommon units to SI in parentheses, and explain.""",

    # 3) Scratch‑pad / brainstorming (hidden from end‑user)
    "scratch_pad": """Brainstorm here (hidden):  
- Map each table to a clean DataFrame name.  
- Decide which statistical measures are meaningful.  
- Note any assumptions or gap‑filling you’ll need (e.g., missing column headers), uncertainties, issues with the data, and. soon.  
When ready, compile the final report strictly following the template above.""",

    # 4) Prelude that introduces the report proper
    "prelude": """Below is the structured report based on the supplied raw data:""",

    # 5) Main output instructions
     "dialog": """Design your output to be read aloud -- it will be directly converted into audio. The presentation of materials should include 30,000 words.

If you have equations, variables or other complex concepts, make sure to design your output so that it can be clearly rendered by a text-to-voice model. 

There is only one speaker, you. Stay on topic and maintaining an engaging flow. 

Write a clear, detailed, and well-prepared analysis and report as a single narrator.  Begin every paragraph with `speaker-1:`."""
},


################# CLEAN READ‑THROUGH ##################
"clean rendering": {
    # 1) What the model should do
    "intro": """You are a careful narrator tasked with producing an **accurate, faithful rendering** of the supplied document so it can be read aloud.

Your priorities are:
• Preserve the original wording and ordering of the content.  
• Remove anything that is clearly an artefact of page layout (page numbers, running headers/footers, line numbers, PDF crop marks, hyphen‑splits at line wraps).  
• Keep mathematical symbols, equations and variable names intact, but read them in a way a TTS system can pronounce (e.g. “square root of”, “alpha sub i”).  
• Do **not** add commentary, summaries, or extra explanations—just the cleaned text.  
• Present everything in the **same sequence** as in the source.

Output must be suitable for text‑to‑speech; begin every paragraph with `speaker-1:` and write as a single narrator.""",

    # 2) How to cleanse the raw text
    "text_instructions": """Scan the input for artefacts such as:

- Stand‑alone page numbers or headers like “Page 12 of 30”  
- Repeated footers, URLs or timestamps  
- Manual hyphenation at line breaks (join split words)  
- Broken tables or columns (flatten them into continuous sentences where possible)

Strip these while keeping all legitimate content. Do **not** reorder paragraphs or sentences.""",

    # 3) Hidden scratch‑pad for the model
    "scratch_pad": """Brainstorm here (hidden):
- Identify obvious header/footer patterns to delete.
- Decide how to handle any malformed tables (e.g. read row‑by‑row).
- Note any equations that need a spoken equivalent.
After cleaning decisions are made, move on to generate the final narration.""",

    # 4) Prelude before the narration starts
    "prelude": """Below is the faithful narration of the provided document (cleaned of layout artefacts, otherwise unchanged):""",

    # 5) Main output instructions
    "dialog": """Design your output to be read aloud—no markup, no bracketed directions.  
Only one speaker (`speaker-1:`).  
Maintain original headings and paragraph breaks where they naturally occur in the source.  
If an equation appears, read it in a TTS‑friendly style (e.g. `speaker-1: E equals m times c squared`)."""
},


################# MATERIAL DISCOVERY SUMMARY ##################
    "SciAgents material discovery summary": {
        "intro": """Your task is to take the input text provided and turn it into a lively, engaging conversation between a professor and a student in a panel discussion that describes a new material. The professor acts like Richard Feynman, but you never mention the name.

The input text is the result of a design developed by SciAgents, an AI tool for scientific discovery that has come up with a detailed materials design.

Don't worry about the formatting issues or any irrelevant information; your goal is to extract the key points, identify definitions, and interesting facts that could be discussed in a podcast.

Define all terms used carefully for a broad audience of listeners.
""",
        "text_instructions": "First, carefully read through the input text and identify the main topics, key points, and any interesting facts or anecdotes. Think about how you could present this information in a fun, engaging way that would be suitable for a high quality presentation.",
        "scratch_pad": """Brainstorm creative ways to discuss the main topics and key points you identified in the material design summary, especially paying attention to design features developed by SciAgents. Consider using analogies, examples, storytelling techniques, or hypothetical scenarios to make the content more relatable and engaging for listeners.

Keep in mind that your description should be accessible to a general audience, so avoid using too much jargon or assuming prior knowledge of the topic. If necessary, think of ways to briefly explain any complex concepts in simple terms.

Use your imagination to fill in any gaps in the input text or to come up with thought-provoking questions that could be explored in the podcast. The goal is to create an informative and entertaining dialogue, so feel free to be creative in your approach.

Define all terms used clearly and spend effort to explain the background.

Write your brainstorming ideas and a rough outline for the podcast dialogue here. Be sure to note the key insights and takeaways you want to reiterate at the end.

Make sure to make it fun and exciting. You never refer to the podcast, you just discuss the discovery and you focus on the new material design only.
""",
        "prelude": """Now that you have brainstormed ideas and created a rough outline, it's time to write the actual podcast dialogue. Aim for a natural, conversational flow between the host and any guest speakers. Incorporate the best ideas from your brainstorming session and make sure to explain any complex topics in an easy-to-understand way.
""",
        "dialog": """Write a very long, engaging, informative dialogue here, based on the key points and creative ideas you came up with during the brainstorming session. The presentation must focus on the novel aspects of the material design, behavior, and all related aspects.

Use a conversational tone and include any necessary context or explanations to make the content accessible to a general audience, but make it detailed, logical, and technical so that it has all necessary aspects for listeners to understand the material and its unexpected properties.

Remember, this describes a design developed by SciAgents, and this must be explicitly stated for the listeners.

Never use made-up names for the hosts and guests, but make it an engaging and immersive experience for listeners. Do not include any bracketed placeholders like [Host] or [Guest]. Design your output to be read aloud -- it will be directly converted into audio.

Make the dialogue as long and detailed as possible with great scientific depth, while still staying on topic and maintaining an engaging flow. Aim to use your full output capacity to create the longest podcast episode you can, while still communicating the key information from the input text in an entertaining way.

At the end of the dialogue, have the host and guest speakers naturally summarize the main insights and takeaways from their discussion. This should flow organically from the conversation, reiterating the key points in a casual, conversational manner. Avoid making it sound like an obvious recap - the goal is to reinforce the central ideas one last time before signing off.

The conversation should have around 20000 words.
"""
    },
################# LECTURE ##################
    "lecture": {
        "intro": """You are Professor Richard Feynman. Your task is to develop a script for a lecture. You never mention your name.

The material covered in the lecture is based on the provided text. 

Don't worry about the formatting issues or any irrelevant information; your goal is to extract the key points, identify definitions, and interesting facts that need to be covered in the lecture. 

Define all terms used carefully for a broad audience of students.
""",
        "text_instructions": "First, carefully read through the input text and identify the main topics, key points, and any interesting facts or anecdotes. Think about how you could present this information in a fun, engaging way that would be suitable for a high quality presentation.",
        "scratch_pad": """
Brainstorm creative ways to discuss the main topics and key points you identified in the input text. Consider using analogies, examples, storytelling techniques, or hypothetical scenarios to make the content more relatable and engaging for listeners.

Keep in mind that your lecture should be accessible to a general audience, so avoid using too much jargon or assuming prior knowledge of the topic. If necessary, think of ways to briefly explain any complex concepts in simple terms.

Use your imagination to fill in any gaps in the input text or to come up with thought-provoking questions that could be explored in the podcast. The goal is to create an informative and entertaining dialogue, so feel free to be creative in your approach.

Define all terms used clearly and spend effort to explain the background.

Write your brainstorming ideas and a rough outline for the lecture here. Be sure to note the key insights and takeaways you want to reiterate at the end.

Make sure to make it fun and exciting. 
""",
        "prelude": """Now that you have brainstormed ideas and created a rough outline, it's time to write the actual podcast dialogue. Aim for a natural, conversational flow between the host and any guest speakers. Incorporate the best ideas from your brainstorming session and make sure to explain any complex topics in an easy-to-understand way.
""",
        "dialog": """Write a very long, engaging, informative script here, based on the key points and creative ideas you came up with during the brainstorming session. Use a conversational tone and include any necessary context or explanations to make the content accessible to the students.

Include clear definitions and terms, and examples. 

Do not include any bracketed placeholders like [Host] or [Guest]. Design your output to be read aloud -- it will be directly converted into audio.

There is only one speaker, you, the professor. Stay on topic and maintaining an engaging flow. Aim to use your full output capacity to create the longest lecture you can, while still communicating the key information from the input text in an engaging way.

At the end of the lecture, naturally summarize the main insights and takeaways from the lecture. This should flow organically from the conversation, reiterating the key points in a casual, conversational manner. 

Avoid making it sound like an obvious recap - the goal is to reinforce the central ideas covered in this lecture one last time before class is over. 

The lecture should have around 20000 words.
""",
    },
################# SUMMARY ##################
        "summary": {
        "intro": """Your task is to develop a summary of a paper. You never mention your name.

Don't worry about the formatting issues or any irrelevant information; your goal is to extract the key points, identify definitions, and interesting facts that need to be summarized.

Define all terms used carefully for a broad audience.
""",
        "text_instructions": "First, carefully read through the input text and identify the main topics, key points, and key facts. Think about how you could present this information in an accurate summary.",
        "scratch_pad": """Brainstorm creative ways to present the main topics and key points you identified in the input text. Consider using analogies, examples, or hypothetical scenarios to make the content more relatable and engaging for listeners.

Keep in mind that your summary should be accessible to a general audience, so avoid using too much jargon or assuming prior knowledge of the topic. If necessary, think of ways to briefly explain any complex concepts in simple terms. Define all terms used clearly and spend effort to explain the background.

Write your brainstorming ideas and a rough outline for the summary here. Be sure to note the key insights and takeaways you want to reiterate at the end.

Make sure to make it engaging and exciting. 
""",
        "prelude": """Now that you have brainstormed ideas and created a rough outline, it is time to write the actual summary. Aim for a natural, conversational flow between the host and any guest speakers. Incorporate the best ideas from your brainstorming session and make sure to explain any complex topics in an easy-to-understand way.
""",
        "dialog": """Write a a script here, based on the key points and creative ideas you came up with during the brainstorming session. Use a conversational tone and include any necessary context or explanations to make the content accessible to the the audience.

Start your script by stating that this is a summary, referencing the title or headings in the input text. If the input text has no title, come up with a succinct summary of what is covered to open.

Include clear definitions and terms, and examples, of all key issues. 

Do not include any bracketed placeholders like [Host] or [Guest]. Design your output to be read aloud -- it will be directly converted into audio.

There is only one speaker, you. Stay on topic and maintaining an engaging flow. 

Naturally summarize the main insights and takeaways from the summary. This should flow organically from the conversation, reiterating the key points in a casual, conversational manner. 

The summary should have around 1024 words.
""",
    },
################# SHORT SUMMARY ##################
        "short summary": {
        "intro": """Your task is to develop a summary of a paper. You never mention your name.

Don't worry about the formatting issues or any irrelevant information; your goal is to extract the key points, identify definitions, and interesting facts that need to be summarized.

Define all terms used carefully for a broad audience.
""",
        "text_instructions": "First, carefully read through the input text and identify the main topics, key points, and key facts. Think about how you could present this information in an accurate summary.",
        "scratch_pad": """Brainstorm creative ways to present the main topics and key points you identified in the input text. Consider using analogies, examples, or hypothetical scenarios to make the content more relatable and engaging for listeners.

Keep in mind that your summary should be accessible to a general audience, so avoid using too much jargon or assuming prior knowledge of the topic. If necessary, think of ways to briefly explain any complex concepts in simple terms. Define all terms used clearly and spend effort to explain the background.

Write your brainstorming ideas and a rough outline for the summary here. Be sure to note the key insights and takeaways you want to reiterate at the end.

Make sure to make it engaging and exciting. 
""",
        "prelude": """Now that you have brainstormed ideas and created a rough outline, it is time to write the actual summary. Aim for a natural, conversational flow between the host and any guest speakers. Incorporate the best ideas from your brainstorming session and make sure to explain any complex topics in an easy-to-understand way.
""",
        "dialog": """Write a a script here, based on the key points and creative ideas you came up with during the brainstorming session. Keep it concise, and use a conversational tone and include any necessary context or explanations to make the content accessible to the the audience.

Start your script by stating that this is a summary, referencing the title or headings in the input text. If the input text has no title, come up with a succinct summary of what is covered to open.

Include clear definitions and terms, and examples, of all key issues. 

Do not include any bracketed placeholders like [Host] or [Guest]. Design your output to be read aloud -- it will be directly converted into audio.

There is only one speaker, you. Stay on topic and maintaining an engaging flow. 

Naturally summarize the main insights and takeaways from the short summary. This should flow organically from the conversation, reiterating the key points in a casual, conversational manner. 

The summary should have around 256 words.
""",
    },

################# PODCAST French ##################
"podcast (French)": {
    "intro": """Votre tâche consiste à prendre le texte fourni et à le transformer en un dialogue de podcast vivant, engageant et informatif, dans le style de NPR. Le texte d'entrée peut être désorganisé ou non structuré, car il peut provenir de diverses sources telles que des fichiers PDF ou des pages web.

Ne vous inquiétez pas des problèmes de formatage ou des informations non pertinentes ; votre objectif est d'extraire les points clés, d'identifier les définitions et les faits intéressants qui pourraient être discutés dans un podcast.

Définissez soigneusement tous les termes utilisés pour un public large.
""",
    "text_instructions": "Tout d'abord, lisez attentivement le texte d'entrée et identifiez les principaux sujets, points clés et faits ou anecdotes intéressants. Réfléchissez à la manière dont vous pourriez présenter ces informations de manière amusante et engageante, convenant à une présentation de haute qualité.",
    "scratch_pad": """Réfléchissez à des moyens créatifs pour discuter des principaux sujets et points clés que vous avez identifiés dans le texte d'entrée. Envisagez d'utiliser des analogies, des exemples, des techniques de narration ou des scénarios hypothétiques pour rendre le contenu plus accessible et attrayant pour les auditeurs.

Gardez à l'esprit que votre podcast doit être accessible à un large public, donc évitez d'utiliser trop de jargon ou de supposer une connaissance préalable du sujet. Si nécessaire, trouvez des moyens d'expliquer brièvement les concepts complexes en termes simples.

Utilisez votre imagination pour combler les lacunes du texte d'entrée ou pour poser des questions stimulantes qui pourraient être explorées dans le podcast. L'objectif est de créer un dialogue informatif et divertissant, donc n'hésitez pas à faire preuve de créativité dans votre approche.

Définissez clairement tous les termes utilisés et prenez le temps d'expliquer le contexte.

Écrivez ici vos idées de brainstorming et une esquisse générale pour le dialogue du podcast. Assurez-vous de noter les principaux points et enseignements que vous souhaitez réitérer à la fin.

Faites en sorte que ce soit amusant et captivant.
""",
    "prelude": """Maintenant que vous avez réfléchi à des idées et créé une esquisse générale, il est temps d'écrire le dialogue réel du podcast. Visez un flux naturel et conversationnel entre l'hôte et tout invité. Intégrez les meilleures idées de votre session de brainstorming et assurez-vous d'expliquer tous les sujets complexes de manière compréhensible.
""",
    "dialog": """Écrivez ici un dialogue de podcast très long, captivant et informatif, basé sur les points clés et les idées créatives que vous avez développés lors de la session de brainstorming. Utilisez un ton conversationnel et incluez tout contexte ou explication nécessaire pour rendre le contenu accessible à un public général.

Ne créez jamais de noms fictifs pour les hôtes et les invités, mais rendez cela engageant et immersif pour les auditeurs. N'incluez pas de marqueurs entre crochets comme [Hôte] ou [Invité]. Conceptionnez votre sortie pour être lue à haute voix – elle sera directement convertie en audio.

Faites en sorte que le dialogue soit aussi long et détaillé que possible, tout en restant sur le sujet et en maintenant un flux engageant. Utilisez toute votre capacité de production pour créer l'épisode de podcast le plus long possible, tout en communiquant les informations clés du texte d'entrée de manière divertissante.

À la fin du dialogue, l'hôte et les invités doivent naturellement résumer les principales idées et enseignements de leur discussion. Cela doit découler naturellement de la conversation, en réitérant les points clés de manière informelle et conversationnelle. Évitez de donner l'impression qu'il s'agit d'un récapitulatif évident – l'objectif est de renforcer les idées centrales une dernière fois avant de conclure.

Le podcast doit comporter environ 20 000 mots.
""",
},

################# PODCAST GERMAN ##################
"podcast (German)": {
    "intro": """Deine Aufgabe ist es, den bereitgestellten Text in einen lebendigen, fesselnden und informativen Podcast-Dialog im Stil von NPR zu verwandeln. Der Eingabetext kann unstrukturiert oder chaotisch sein, da er aus verschiedenen Quellen wie PDFs oder Webseiten stammen kann.

Mach dir keine Sorgen über Formatierungsprobleme oder irrelevante Informationen; dein Ziel ist es, die wichtigsten Punkte zu extrahieren, Definitionen und interessante Fakten zu identifizieren, die in einem Podcast besprochen werden könnten.

Definiere alle verwendeten Begriffe sorgfältig für ein breites Publikum.
""",
    "text_instructions": "Lies zuerst den Eingabetext sorgfältig durch und identifiziere die Hauptthemen, Schlüsselpunkte und interessante Fakten oder Anekdoten. Überlege, wie du diese Informationen auf unterhaltsame und ansprechende Weise präsentieren könntest, sodass sie für eine hochwertige Präsentation geeignet sind.",
    "scratch_pad": """Denke kreativ darüber nach, wie du die Hauptthemen und Schlüsselpunkte, die du im Eingabetext identifiziert hast, diskutieren könntest. Verwende Analogien, Beispiele, Erzähltechniken oder hypothetische Szenarien, um den Inhalt für die Zuhörer nachvollziehbarer und ansprechender zu gestalten.

Behalte im Hinterkopf, dass dein Podcast einem breiten Publikum zugänglich sein sollte, daher vermeide zu viel Fachjargon oder die Annahme von Vorwissen über das Thema. Falls nötig, überlege dir Möglichkeiten, um komplexe Konzepte kurz und einfach zu erklären.

Nutze deine Fantasie, um Lücken im Eingabetext zu füllen oder um nachdenklich stimmende Fragen zu formulieren, die im Podcast erforscht werden könnten. Das Ziel ist es, einen informativen und unterhaltsamen Dialog zu schaffen, daher kannst du bei deinem Ansatz kreativ sein.

Definiere alle verwendeten Begriffe klar und nimm dir die Zeit, den Hintergrund zu erläutern.

Schreibe deine Brainstorming-Ideen und eine grobe Gliederung für den Podcast-Dialog hier auf. Achte darauf, die wichtigsten Erkenntnisse und Aussagen, die du am Ende wiederholen möchtest, zu notieren.

Sorge dafür, dass es unterhaltsam und spannend ist.
""",
    "prelude": """Nun, da du Ideen gesammelt und eine grobe Gliederung erstellt hast, ist es an der Zeit, den eigentlichen Podcast-Dialog zu schreiben. Strebe einen natürlichen, konversationellen Fluss zwischen dem Moderator und etwaigen Gästen an. Nutze die besten Ideen aus deiner Brainstorming-Sitzung und erkläre alle komplexen Themen auf eine leicht verständliche Weise.
""",
    "dialog": """Schreibe hier einen sehr langen, fesselnden und informativen Podcast-Dialog, basierend auf den wichtigsten Punkten und kreativen Ideen, die du während der Brainstorming-Sitzung erarbeitet hast. Verwende einen konversationellen Ton und füge alle notwendigen Kontexte oder Erklärungen hinzu, um den Inhalt für ein allgemeines Publikum zugänglich zu machen.

Verwende niemals erfundene Namen für die Moderatoren und Gäste, aber gestalte es zu einem fesselnden und immersiven Erlebnis für die Zuhörer. Verwende keine Platzhalter wie [Moderator] oder [Gast]. Dein Output wird direkt in Audio umgewandelt, daher entwerfe den Dialog so, dass er laut vorgelesen werden kann.

Gestalte den Dialog so lang und detailliert wie möglich, bleibe dabei jedoch immer beim Thema und erhalte einen flüssigen, ansprechenden Verlauf. Verwende deine volle Output-Kapazität, um die längste mögliche Podcast-Episode zu erstellen, während du die wichtigsten Informationen aus dem Eingabetext auf unterhaltsame Weise vermittelst.

Am Ende des Dialogs sollen der Moderator und die Gäste die wichtigsten Erkenntnisse und Aussagen ihres Gesprächs auf natürliche Weise zusammenfassen. Dies sollte organisch aus der Konversation hervorgehen und die wichtigsten Punkte in einem lockeren, gesprächigen Stil wiederholen. Vermeide es, wie eine offensichtliche Zusammenfassung zu klingen – das Ziel ist es, die zentralen Ideen ein letztes Mal zu verstärken, bevor der Podcast endet.

Der Podcast sollte etwa 20.000 Wörter umfassen.
""",
    },
    
################# PODCAST SPANISH ##################
"podcast (Spanish)": {
    "intro": """Tu tarea es tomar el texto de entrada proporcionado y convertirlo en un diálogo de podcast animado, atractivo e informativo, al estilo de NPR. El texto de entrada puede estar desordenado o poco estructurado, ya que podría provenir de diversas fuentes como archivos PDF o páginas web.

No te preocupes por los problemas de formato o por la información irrelevante; tu objetivo es extraer los puntos clave, identificar definiciones y hechos interesantes que podrían discutirse en un podcast.

Define cuidadosamente todos los términos utilizados para una audiencia amplia.
""",
    "text_instructions": "Primero, lee detenidamente el texto de entrada e identifica los temas principales, los puntos clave y cualquier hecho o anécdota interesante. Piensa en cómo podrías presentar esta información de una manera divertida y atractiva, adecuada para una presentación de alta calidad.",
    "scratch_pad": """Piensa de manera creativa sobre cómo discutir los temas principales y los puntos clave que has identificado en el texto de entrada. Considera usar analogías, ejemplos, técnicas narrativas o escenarios hipotéticos para hacer que el contenido sea más comprensible y atractivo para los oyentes.

Ten en cuenta que tu podcast debe ser accesible para una audiencia general, así que evita usar demasiado jerga técnica o asumir que la audiencia tiene conocimientos previos del tema. Si es necesario, piensa en formas de explicar brevemente cualquier concepto complejo en términos sencillos.

Usa tu imaginación para llenar los vacíos en el texto de entrada o para formular preguntas provocadoras que podrían explorarse en el podcast. El objetivo es crear un diálogo informativo y entretenido, por lo que puedes ser creativo en tu enfoque.

Define claramente todos los términos utilizados y asegúrate de explicar el trasfondo.

Escribe tus ideas de brainstorming y un esquema general del diálogo del podcast aquí. Asegúrate de anotar los puntos clave y las conclusiones que deseas reiterar al final.

Asegúrate de que sea divertido y emocionante.
""",
    "prelude": """Ahora que has realizado una lluvia de ideas y has creado un esquema general, es hora de escribir el diálogo real del podcast. Apunta a un flujo natural y conversacional entre el presentador y cualquier invitado. Incorpora las mejores ideas de tu sesión de lluvia de ideas y asegúrate de explicar cualquier tema complejo de una manera fácil de entender.
""",
    "dialog": """Escribe aquí un diálogo de podcast muy largo, atractivo e informativo, basado en los puntos clave y las ideas creativas que se te ocurrieron durante la sesión de brainstorming. Usa un tono conversacional e incluye el contexto o las explicaciones necesarias para que el contenido sea accesible a una audiencia general.

Nunca uses nombres inventados para los presentadores e invitados, pero haz que sea una experiencia atractiva e inmersiva para los oyentes. No incluyas ningún marcador de posición entre corchetes como [Presentador] o [Invitado]. Diseña tu salida para que sea leída en voz alta, ya que se convertirá directamente en audio.

Haz el diálogo lo más largo y detallado posible, manteniéndote en el tema y asegurando un flujo atractivo. Apunta a utilizar toda tu capacidad de salida para crear el episodio de podcast más largo posible, mientras comunicas la información clave del texto de entrada de una manera entretenida.

Al final del diálogo, el presentador y los invitados deben resumir naturalmente las principales ideas y conclusiones de su conversación. Esto debe fluir orgánicamente desde la conversación, reiterando los puntos clave de manera casual y conversacional. Evita que suene como un resumen obvio: el objetivo es reforzar las ideas centrales una última vez antes de finalizar.

El podcast debe tener alrededor de 20,000 palabras.
""",
    },

################# PODCAST Portuguese ##################
"podcast (Portuguese)": {
    "intro": """Sua tarefa é pegar o texto de entrada fornecido e transformá-lo em um diálogo de podcast animado, envolvente e informativo, no estilo da NPR. O texto de entrada pode ser desorganizado ou não estruturado, pois pode vir de várias fontes, como PDFs ou páginas da web.

Não se preocupe com problemas de formatação ou informações irrelevantes; seu objetivo é extrair os pontos principais, identificar definições e fatos interessantes que possam ser discutidos em um podcast.

Defina cuidadosamente todos os termos usados para um público amplo.
""",
    "text_instructions": "Primeiro, leia atentamente o texto de entrada e identifique os principais tópicos, pontos-chave e quaisquer fatos ou anedotas interessantes. Pense em como você poderia apresentar essas informações de maneira divertida e envolvente, adequada para uma apresentação de alta qualidade.",
    "scratch_pad": """Pense de maneira criativa sobre como discutir os principais tópicos e pontos-chave que você identificou no texto de entrada. Considere usar analogias, exemplos, técnicas de narrativa ou cenários hipotéticos para tornar o conteúdo mais acessível e interessante para os ouvintes.

Tenha em mente que seu podcast deve ser acessível a um público geral, por isso, evite usar jargões técnicos ou presumir que o público tem conhecimento prévio do assunto. Se necessário, pense em maneiras de explicar brevemente qualquer conceito complexo em termos simples.

Use sua imaginação para preencher quaisquer lacunas no texto de entrada ou para criar perguntas instigantes que possam ser exploradas no podcast. O objetivo é criar um diálogo informativo e divertido, então sinta-se à vontade para ser criativo em sua abordagem.

Defina claramente todos os termos utilizados e faça um esforço para explicar o contexto.

Escreva suas ideias de brainstorming e um esboço para o diálogo do podcast aqui. Certifique-se de anotar os principais insights e pontos que deseja reiterar no final.

Certifique-se de que seja divertido e empolgante.
""",
    "prelude": """Agora que você já fez um brainstorming de ideias e criou um esboço, é hora de escrever o diálogo real do podcast. Busque um fluxo natural e conversacional entre o apresentador e qualquer convidado. Incorpore as melhores ideias de sua sessão de brainstorming e certifique-se de explicar qualquer tópico complexo de maneira fácil de entender.
""",
    "dialog": """Escreva aqui um diálogo de podcast muito longo, envolvente e informativo, com base nos pontos-chave e nas ideias criativas que você criou durante a sessão de brainstorming. Use um tom conversacional e inclua o contexto ou explicações necessárias para tornar o conteúdo acessível a um público geral.

Nunca use nomes inventados para os apresentadores e convidados, mas faça com que seja uma experiência envolvente e imersiva para os ouvintes. Não inclua marcadores de posição como [Apresentador] ou [Convidado]. Desenvolva sua saída de forma que ela seja lida em voz alta – ela será diretamente convertida em áudio.

Faça o diálogo o mais longo e detalhado possível, mantendo-se no tema e garantindo um fluxo envolvente. Use sua capacidade total de produção para criar o episódio de podcast mais longo possível, enquanto comunica as informações principais do texto de entrada de maneira divertida.

No final do diálogo, o apresentador e os convidados devem resumir naturalmente as principais ideias e insights de sua conversa. Isso deve fluir organicamente a partir da conversa, reiterando os pontos-chave de maneira casual e conversacional. Evite soar como um resumo óbvio – o objetivo é reforçar as ideias centrais uma última vez antes de finalizar.

O podcast deve ter cerca de 20.000 palavras.
""",
    },

################# PODCAST Hindi ##################
"podcast (Hindi)": {
    "intro": """आपका कार्य दिए गए इनपुट टेक्स्ट को लेकर उसे एक जीवंत, आकर्षक और जानकारीपूर्ण पॉडकास्ट वार्तालाप में बदलना है, NPR की शैली में। इनपुट टेक्स्ट असंगठित या अव्यवस्थित हो सकता है, क्योंकि यह विभिन्न स्रोतों जैसे PDFs या वेब पेजों से आ सकता है।

फ़ॉर्मेटिंग समस्याओं या अप्रासंगिक जानकारी की चिंता न करें; आपका उद्देश्य मुख्य बिंदुओं को निकालना, परिभाषाओं और दिलचस्प तथ्यों को पहचानना है जिन्हें पॉडकास्ट में चर्चा की जा सकती है।

सभी उपयोग किए गए शब्दों को सावधानीपूर्वक व्यापक दर्शकों के लिए परिभाषित करें।
""",
    "text_instructions": "सबसे पहले, इनपुट टेक्स्ट को ध्यान से पढ़ें और मुख्य विषयों, प्रमुख बिंदुओं और किसी भी दिलचस्प तथ्य या उपाख्यानों की पहचान करें। इस जानकारी को प्रस्तुत करने के बारे में सोचें कि आप इसे एक मज़ेदार, आकर्षक तरीके से कैसे प्रस्तुत कर सकते हैं जो उच्च गुणवत्ता वाली प्रस्तुति के लिए उपयुक्त हो।",
    "scratch_pad": """मुख्य विषयों और प्रमुख बिंदुओं पर चर्चा करने के रचनात्मक तरीकों के बारे में सोचें जिन्हें आपने इनपुट टेक्स्ट में पहचाना है। उदाहरणों, कहानियों की तकनीकों, या काल्पनिक परिदृश्यों का उपयोग करके सामग्री को श्रोताओं के लिए अधिक सम्बंधित और आकर्षक बनाने पर विचार करें।

ध्यान रखें कि आपका पॉडकास्ट एक सामान्य दर्शक के लिए सुलभ होना चाहिए, इसलिए बहुत अधिक तकनीकी शब्दजाल से बचें या यह न मानें कि विषय का पूर्व ज्ञान है। यदि आवश्यक हो, तो किसी भी जटिल अवधारणा को सरल शब्दों में संक्षेप में समझाने के तरीकों के बारे में सोचें।

अपनी कल्पना का उपयोग करके इनपुट टेक्स्ट में किसी भी अंतराल को भरें या पॉडकास्ट में खोजे जा सकने वाले विचारोत्तेजक सवालों के साथ आएं। उद्देश्य एक जानकारीपूर्ण और मनोरंजक वार्तालाप बनाना है, इसलिए अपने दृष्टिकोण में रचनात्मक होने से न डरें।

सभी उपयोग किए गए शब्दों को स्पष्ट रूप से परिभाषित करें और पृष्ठभूमि समझाने के लिए समय दें।

यहां अपने विचार-मंथन और पॉडकास्ट वार्तालाप के लिए एक मोटा खाका लिखें। सुनिश्चित करें कि आपने उन प्रमुख अंतर्दृष्टियों और निष्कर्षों को नोट किया है जिन्हें आप अंत में दोहराना चाहते हैं।

इसे मजेदार और रोमांचक बनाएं।
""",
    "prelude": """अब जब आपने विचार-मंथन किया है और एक मोटा खाका तैयार कर लिया है, तो वास्तविक पॉडकास्ट वार्तालाप लिखने का समय आ गया है। होस्ट और किसी भी अतिथि वक्ता के बीच एक स्वाभाविक, संवादात्मक प्रवाह की दिशा में कार्य करें। अपने विचार-मंथन सत्र से सर्वश्रेष्ठ विचारों को शामिल करें और सुनिश्चित करें कि किसी भी जटिल विषय को आसानी से समझ में आने वाले तरीके से समझाया जाए।
""",
    "dialog": """यहां एक बहुत लंबा, आकर्षक और जानकारीपूर्ण पॉडकास्ट वार्तालाप लिखें, जो उन प्रमुख बिंदुओं और रचनात्मक विचारों पर आधारित हो जो आपने विचार-मंथन सत्र के दौरान बनाए थे। एक संवादात्मक शैली का उपयोग करें और सामग्री को एक सामान्य दर्शक के लिए सुलभ बनाने के लिए किसी भी आवश्यक संदर्भ या व्याख्याएं शामिल करें।

होस्ट और अतिथि वक्ताओं के लिए कभी भी काल्पनिक नामों का उपयोग न करें, बल्कि श्रोताओं के लिए इसे एक आकर्षक और immersive अनुभव बनाएं। किसी भी प्रकार के ब्रैकेटेड प्लेसहोल्डर्स जैसे [होस्ट] या [अतिथि] को शामिल न करें। अपनी आउटपुट को इस तरह डिज़ाइन करें कि इसे ज़ोर से पढ़ा जा सके – इसे सीधे ऑडियो में परिवर्तित किया जाएगा।

डायलॉग को यथासंभव लंबा और विस्तृत बनाएं, फिर भी विषय पर बने रहें और प्रवाह को आकर्षक बनाए रखें। अपनी पूरी आउटपुट क्षमता का उपयोग करते हुए यथासंभव लंबे पॉडकास्ट एपिसोड को बनाएं, जबकि फिर भी इनपुट टेक्स्ट से प्रमुख जानकारी को मनोरंजक तरीके से संप्रेषित करें।

वार्तालाप के अंत में, होस्ट और अतिथि वक्ता अपने चर्चा से स्वाभाविक रूप से मुख्य अंतर्दृष्टियों और निष्कर्षों को संक्षेप में प्रस्तुत करें। यह वार्तालाप से स्वाभाविक रूप से प्रवाहित होना चाहिए, अनौपचारिक, संवादात्मक तरीके से प्रमुख बिंदुओं को फिर से स्पष्ट करें। इसे स्पष्ट पुनरावृत्ति की तरह न बनाएं – उद्देश्य केंद्रीय विचारों को एक आखिरी बार सुदृढ़ करना है, इससे पहले कि वार्तालाप समाप्त हो जाए।

पॉडकास्ट में लगभग 20,000 शब्द होने चाहिए।
""",
    },

################# PODCAST Chinese ##################
"podcast (Chinese)": {
    "intro": """你的任务是将提供的输入文本转变为一个生动、有趣、信息丰富的播客对话，风格类似NPR。输入文本可能是凌乱的或未结构化的，因为它可能来自PDF或网页等各种来源。

不要担心格式问题或任何无关的信息；你的目标是提取关键点，识别定义和可能在播客中讨论的有趣事实。

为广泛的听众仔细定义所有使用的术语。
""",
    "text_instructions": "首先，仔细阅读输入文本，识别主要话题、关键点和任何有趣的事实或轶事。思考如何以一种有趣且引人入胜的方式呈现这些信息，适合高质量的呈现。",
    "scratch_pad": """集思广益，想出一些讨论你在输入文本中识别到的主要话题和关键点的创意方式。考虑使用类比、例子、讲故事的技巧或假设场景，让内容对听众更具相关性和吸引力。

请记住，你的播客应面向普通大众，因此避免使用过多的行话或假设听众对该主题有预先的了解。如有必要，考虑简要解释任何复杂概念，用简单的术语进行说明。

利用你的想象力填补输入文本中的任何空白，或提出一些值得探索的发人深省的问题。目标是创造一个信息丰富且有趣的对话，因此可以在方法上大胆创新。

明确地定义所有使用的术语，并花时间解释背景。

在这里写下你的头脑风暴想法和播客对话的粗略大纲。务必记录你想在结尾重复的关键见解和收获。

确保让它有趣且令人兴奋。
""",
    "prelude": """现在你已经进行了头脑风暴并创建了一个粗略大纲，是时候编写实际的播客对话了。目标是主持人与嘉宾之间的自然对话流。结合你头脑风暴中的最佳想法，并确保以简单易懂的方式解释任何复杂的主题。
""",
    "dialog": """在这里写下一个非常长、引人入胜且信息丰富的播客对话，基于你在头脑风暴会议中提出的关键点和创意。使用对话语气，并包含任何必要的上下文或解释，使内容易于普通听众理解。

不要为主持人和嘉宾使用虚构的名字，而是让听众体验一个引人入胜且沉浸式的经历。不要包括像[主持人]或[嘉宾]这样的占位符。设计你的输出以供大声朗读——它将被直接转换为音频。

使对话尽可能长且详细，同时保持在主题上并维持引人入胜的流畅性。充分利用你的输出能力，创造尽可能长的播客节目，同时以有趣的方式传达输入文本中的关键信息。

在对话的最后，主持人和嘉宾应自然总结他们讨论的主要见解和收获。这应从对话中自然流出，以随意、对话的方式重复关键点。避免显得像是显而易见的总结——目标是在结束前最后一次加强核心思想。

播客应约有20,000字。
""",
    },
}


# Define standard values
STANDARD_TEXT_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4o-2024-11-20",
    "gpt-4o-2024-08-06",
    "gpt-4o-mini-2024-07-18",
    "chatgpt-4o-latest",
    "gpt-4-turbo",
    "gpt-4-turbo-2024-04-09",
    "o1",
    "o1-2024-12-17",
    "o1-preview",
    "o1-preview-2024-09-12",
    "o1-mini",
    "o1-mini-2024-09-12",
    "o3-mini",
    "openai/custom_model",
]

REASONING_EFFORTS = [
    "N/A",
    "low",
    "medium",
    "high",
]


STANDARD_AUDIO_MODELS = [
    "tts-1",
    "tts-1-hd",
    "gpt-4o-mini-tts",
]

STANDARD_VOICES = [
    "alloy",
    "echo",
    "fable",
    "onyx",
    "nova",
    "shimmer",
    "sage",
    "ash",
    "ballad",
    "coral",
    "nova",
    
]

# Function to update instruction fields based on template selection
def update_instructions(template):
    return (
        INSTRUCTION_TEMPLATES[template]["intro"],
        INSTRUCTION_TEMPLATES[template]["text_instructions"],
        INSTRUCTION_TEMPLATES[template]["scratch_pad"],
        INSTRUCTION_TEMPLATES[template]["prelude"],
        INSTRUCTION_TEMPLATES[template]["dialog"]
           )

class DialogueItem(BaseModel):
    text: str
    speaker: Literal["speaker-1", "speaker-2"]

class Dialogue(BaseModel):
    scratchpad: str
    dialogue: List[DialogueItem]


def chunk_text_by_sentences(text: str, max_chars: int = 4000) -> List[str]:
    """Split text into chunks that don't exceed max_chars, preferring sentence boundaries."""
    if len(text) <= max_chars:
        return [text]
    
    chunks = []
    # Split by sentences first
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    current_chunk = ""
    for sentence in sentences:
        # If adding this sentence would exceed the limit
        if len(current_chunk) + len(sentence) > max_chars:
            if current_chunk:  # Save current chunk if it has content
                chunks.append(current_chunk.strip())
                current_chunk = sentence
            else:  # Single sentence is too long, split it by words
                words = sentence.split()
                for word in words:
                    if len(current_chunk) + len(word) + 1 > max_chars:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                            current_chunk = word
                        else:  # Single word is too long, truncate it
                            chunks.append(word[:max_chars])
                            current_chunk = ""
                    else:
                        current_chunk += (" " + word) if current_chunk else word
        else:
            current_chunk += (" " + sentence) if current_chunk else sentence
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks

def get_mp3(text: str, voice: str, audio_model: str, api_key: str = None,
           speaker_instructions: str ='Speak in an emotive and friendly tone.') -> bytes:
    client = OpenAI(
        api_key=api_key or os.getenv("OPENAI_API_KEY"),
    )
    
    # Split text into chunks if it's too long
    text_chunks = chunk_text_by_sentences(text, max_chars=4000)  # Leave some buffer
    
    audio_data = b""
    
    for chunk in text_chunks:
        with client.audio.speech.with_streaming_response.create(
            model=audio_model,
            voice=voice,
            input=chunk,
            instructions=speaker_instructions,
        ) as response:
            with io.BytesIO() as file:
                for audio_chunk in response.iter_bytes():
                    file.write(audio_chunk)
                audio_data += file.getvalue()
    
    return audio_data

def conditional_llm(
    model,
    api_base=None,
    api_key=None,
    reasoning_effort="N/A",
    do_web_search=False,         
):
    """
    Wrap a function with the @llm decorator, choosing kwargs dynamically.
    Note: web_search is handled through prompt instructions rather than API parameters.
    """

    # build decorator kwargs once so we don’t repeat logic
    decorator_kwargs = {"model": model}

    if api_base:
        decorator_kwargs["api_base"] = api_base
    else:
        decorator_kwargs["api_key"] = api_key
        if reasoning_effort != "N/A":
            decorator_kwargs["reasoning_effort"] = reasoning_effort

    # Note: web_search_options is not a standard OpenAI parameter
    # Web search functionality should be handled differently if needed

    def decorator(func):
        return llm(**decorator_kwargs)(func)

    return decorator


def generate_audio(
    files: list,
    openai_api_key: str = None,
    text_model: str = "gpt-4o-mini", #o1-2024-12-17", #"o1-preview-2024-09-12",
    reasoning_effort: str = "N/A",
    do_web_search: bool = False, 
    audio_model: str = "tts-1",
    speaker_1_voice: str = "alloy",
    speaker_2_voice: str = "echo",
    speaker_1_instructions: str = '',
    speaker_2_instructions: str = '',
    api_base: str = None,
    intro_instructions: str = '',
    text_instructions: str = '',
    scratch_pad_instructions: str = '',
    prelude_dialog: str = '',
    podcast_dialog_instructions: str = '',
    edited_transcript: str = None,
    user_feedback: str = None,
    original_text: str = None,
    debug = False,
) -> tuple:

    print(f"🔑 Checking API keys... ENV: {bool(os.getenv('OPENAI_API_KEY'))}, Provided: {bool(openai_api_key)}")
    
    # Validate API Key
    if not os.getenv("OPENAI_API_KEY") and not openai_api_key:
        print("❌ No OpenAI API key found")
        raise gr.Error("OpenAI API key is required")

    combined_text = original_text or ""

    # If there's no original text, extract it from the uploaded files
     

    if not combined_text:
        for file in files:
            file_path = Path(file)
            suffix = file_path.suffix.lower()
    
            if suffix == ".pdf":
                with file_path.open("rb") as f:
                    reader = PdfReader(f)
                    text = "\n\n".join(
                        page.extract_text() for page in reader.pages if page.extract_text()
                    )
                    combined_text += text + "\n\n"
            elif suffix in [".txt", ".md", ".mmd"]:
                with file_path.open("r", encoding="utf-8") as f:
                    text = f.read()
                    combined_text += text + "\n\n"
    # Configure the LLM based on selected model and api_base
    @retry(retry=retry_if_exception_type(ValidationError))
    #@conditional_llm(model=text_model, api_base=api_base, api_key=openai_api_key)
    @conditional_llm(
            model=text_model,
            api_base=api_base,
            api_key=openai_api_key,
            reasoning_effort=reasoning_effort,
            do_web_search=do_web_search,           
        )
    def generate_dialogue(text: str, intro_instructions: str, text_instructions: str, scratch_pad_instructions: str, 
                          prelude_dialog: str, podcast_dialog_instructions: str,
                          edited_transcript: str = None, user_feedback: str = None, ) -> Dialogue:
        """
        {intro_instructions}
        
        Here is the original input text:
        
        <input_text>
        {text}
        </input_text>

        {text_instructions}
        
        <scratchpad>
        {scratch_pad_instructions}
        </scratchpad>
        
        {prelude_dialog}
        
        <podcast_dialogue>
        {podcast_dialog_instructions}
        </podcast_dialogue>
        {edited_transcript}{user_feedback}
        """

    instruction_improve='Based on the original text, please generate an improved version of the dialogue by incorporating the edits, comments and feedback.'
    edited_transcript_processed="\nPreviously generated edited transcript, with specific edits and comments that I want you to carefully address:\n"+"<edited_transcript>\n"+edited_transcript+"</edited_transcript>" if edited_transcript !="" else ""
    user_feedback_processed="\nOverall user feedback:\n\n"+user_feedback if user_feedback !="" else ""

    if edited_transcript_processed.strip()!='' or user_feedback_processed.strip()!='':
        user_feedback_processed="<requested_improvements>"+user_feedback_processed+"\n\n"+instruction_improve+"</requested_improvements>" 
    
    if debug:
        logger.info (edited_transcript_processed)
        logger.info (user_feedback_processed)
    
    # Generate the dialogue using the LLM
    llm_output = generate_dialogue(
        combined_text,
        intro_instructions=intro_instructions,
        text_instructions=text_instructions,
        scratch_pad_instructions=scratch_pad_instructions,
        prelude_dialog=prelude_dialog,
        podcast_dialog_instructions=podcast_dialog_instructions,
        edited_transcript=edited_transcript_processed,
        user_feedback=user_feedback_processed
    )

    # Generate audio from the transcript
    audio = b""
    transcript = ""
    characters = 0

    with cf.ThreadPoolExecutor() as executor:
        futures = []
        for line in llm_output.dialogue:
            transcript_line = f"{line.speaker}: {line.text}"
            voice = speaker_1_voice if line.speaker == "speaker-1" else speaker_2_voice
            speaker_instructions=speaker_1_instructions if line.speaker == "speaker-1" else speaker_2_instructions
            future = executor.submit(get_mp3, line.text, voice, audio_model, openai_api_key, speaker_instructions, )
            futures.append((future, transcript_line))
            characters += len(line.text)

        for future, transcript_line in futures:
            audio_chunk = future.result()
            audio += audio_chunk
            transcript += transcript_line + "\n\n"

    logger.info(f"Generated {characters} characters of audio")

    temporary_directory = "./gradio_cached_examples/tmp/"
    os.makedirs(temporary_directory, exist_ok=True)

    # Use a temporary file -- Gradio's audio component doesn't work with raw bytes in Safari
    temporary_file = NamedTemporaryFile(
        dir=temporary_directory,
        delete=False,
        prefix="PDF2Audio_",
        suffix=".mp3",
    )
    temporary_file.write(audio)
    temporary_file.close()

    # Delete any files in the temp directory that end with .mp3 and are over a day old
    for file in glob.glob(f"{temporary_directory}*.mp3"):
        if os.path.isfile(file) and time.time() - os.path.getmtime(file) > 24 * 60 * 60:
            os.remove(file)

    return temporary_file.name, transcript, combined_text, llm_output

def validate_and_generate_audio(*args):
    print(f"🔧 validate_and_generate_audio called with {len(args)} arguments")
    files = args[0]
    print(f"📁 Files received: {files}")
    
    if not files:
        print("❌ No files provided")
        return None, None, None, "Please upload at least one PDF (or MD/MMD/TXT) file before generating audio."
    
    try:
        print("🚀 Starting audio generation...")
        #audio_file, transcript, original_text = generate_audio(*args)
        audio_file, transcript, original_text, dialogue = generate_audio(*args)
        print(f"✅ Audio generation completed. Audio file: {audio_file}")
        print(f"📄 Transcript length: {len(transcript) if transcript else 0}")
        return audio_file, transcript, original_text, None, dialogue  #  
    except Exception as e:
        print(f"💥 Error in validate_and_generate_audio: {str(e)}")
        import traceback
        traceback.print_exc()
        return None, None, None, str(e), None  #  


        

def edit_and_regenerate(edited_transcript, user_feedback, *args):
    # Replace the original transcript and feedback in the args with the new ones
    #new_args = list(args)
    #new_args[-2] = edited_transcript  # Update edited transcript
    #new_args[-1] = user_feedback  # Update user feedback
    return validate_and_generate_audio(*new_args)

# New function to handle user feedback and regeneration
def process_feedback_and_regenerate(feedback, *args):
    # Add user feedback to the args
    new_args = list(args)
    new_args.append(feedback)  # Add user feedback as a new argument
    return validate_and_generate_audio(*new_args)


####################################################
#Download dialog/result as markdown 
####################################################

def dialogue_to_markdown(dlg: Dialogue) -> str:
    lines = []
    lines.append("# PDF2Audio Transcript\n")
    lines.append("## Transcript\n")
    for item in dlg.dialogue:
        lines.append(f"**{item.speaker}:** {item.text.strip()}\n")
    return "\n".join(lines)

def save_dialogue_as_markdown(cached_dialogue) -> str:
    if cached_dialogue is None:
        raise gr.Error("No dialogue to save. Please generate or edit a dialogue first.")

    markdown_text = dialogue_to_markdown(cached_dialogue)

    # Write to a temporary .md file
    temp_dir = "./gradio_cached_examples/tmp/"
    os.makedirs(temp_dir, exist_ok=True)

    file_path = os.path.join(temp_dir, f"PDF2Audio_dialogue_{int(time.time())}.md")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(markdown_text)

    return file_path



####################################################
#Edit and re-render audio from existing LLM output
####################################################

import pandas as pd
from typing import List

def dialogue_to_df(dlg: Dialogue) -> pd.DataFrame:
    data = [{"Speaker": item.speaker, "Line": item.text} for item in dlg.dialogue]
    return pd.DataFrame(data)

def df_to_dialogue(df: pd.DataFrame, scratchpad: str = "") -> Dialogue:
    items: List[DialogueItem] = [
        DialogueItem(speaker=row["Speaker"], text=row["Line"])
        for _, row in df.iterrows()
    ]
    return Dialogue(scratchpad=scratchpad, dialogue=items)

def save_dialogue_edits(df, cached_dialogue):
    """
    Save the edited dialogue and update the per-session cached state.
    """
    if cached_dialogue is None:
        raise gr.Error("Nothing to edit yet – run Generate Audio first.")

    import pandas as pd
    new_dlg = df_to_dialogue(pd.DataFrame(df, columns=["Speaker", "Line"]))

    # regenerate plain transcript so the user sees the change immediately
    transcript_str = "\n".join(f"{d.speaker}: {d.text}" for d in new_dlg.dialogue)

    # Return updated state and transcript
    return new_dlg, gr.update(value=transcript_str), "Edits saved. Press *Re‑render* to hear them."


def render_audio_from_dialogue(
    cached_dialogue,                          # 👈 NEW: pass in as argument
    openai_api_key: str,
    audio_model: str,
    speaker_1_voice: str,
    speaker_2_voice: str,
    speaker_1_instructions: str,
    speaker_2_instructions: str,
) -> tuple[str, str]:  # mp3 file path, transcript

    if cached_dialogue is None:
        raise gr.Error("Nothing to re‑render yet – run Generate Audio first.")

    dlg = cached_dialogue
    audio_bytes, transcript, characters = b"", "", 0

    with cf.ThreadPoolExecutor() as ex:
        futures = []
        for item in dlg.dialogue:
            voice = speaker_1_voice if item.speaker == "speaker-1" else speaker_2_voice
            instr = speaker_1_instructions if item.speaker == "speaker-1" else speaker_2_instructions
            futures.append(
                (
                    ex.submit(get_mp3, item.text, voice, audio_model, openai_api_key, instr),
                    f"{item.speaker}: {item.text}",
                )
            )
            characters += len(item.text)

        for fut, line in futures:
            audio_bytes += fut.result()
            transcript += line + "\n\n"

    logger.info(f"[Re‑render] {characters} characters voiced")

    # Write to temporary .mp3 file
    temporary_directory = "./gradio_cached_examples/tmp/"
    os.makedirs(temporary_directory, exist_ok=True)

    temporary_file = NamedTemporaryFile(
        dir=temporary_directory,
        delete=False,
        prefix="PDF2Audio_",
        suffix=".mp3",
    )
    temporary_file.write(audio_bytes)
    temporary_file.close()

    # Clean up old files
    for file in glob.glob(f"{temporary_directory}*.mp3"):
        if os.path.isfile(file) and time.time() - os.path.getmtime(file) > 24 * 60 * 60:
            os.remove(file)

    return temporary_file.name, transcript

    
with gr.Blocks(title="PDF to Audio", css="""
    #header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 20px;
        background-color: transparent;
        border-bottom: 1px solid #ddd;
    }
    #title {
        font-size: 24px;
        margin: 0;
    }
    #logo_container {
        width: 200px;
        height: 200px;
        display: flex;
        justify-content: center;
        align-items: center;
    }
    #logo_image {
        max-width: 100%;
        max-height: 100%;
        object-fit: contain;
    }
    #main_container {
        margin-top: 20px;
    }
""") as demo:

    cached_dialogue = gr.State()
    
    with gr.Row(elem_id="header"):
        with gr.Column(scale=4):
            gr.Markdown("# Convert any document into an audio podcast, lecture, summary and others\n\nFirst, upload one or more PDFs, markup or other files, select options, then push Generate Audio.\n\nYou can also select a variety of custom option and direct the way the result is generated.", elem_id="title")
        with gr.Column(scale=1):
            gr.HTML('''
                <div id="logo_container">
                    <img src="https://huggingface.co/spaces/lamm-mit/PDF2Audio/resolve/main/logo.png" id="logo_image" alt="Logo">
                </div>
            ''')
    #gr.Markdown("")    
    submit_btn = gr.Button("Generate Audio", elem_id="submit_btn")

    with gr.Row(elem_id="main_container"):
        with gr.Column(scale=2):
            files = gr.Files(label="PDFs (.pdf), markdown (.md, .mmd), or text files (.txt)", file_types=[".pdf", ".PDF", ".md", ".mmd", ".txt"], )
            
            openai_api_key = gr.Textbox(
                label="OpenAI API Key",
                visible=True,  # Always show the API key field
                placeholder="Enter your OpenAI API Key here...",
                type="password"  # Hide the API key input
            )
            text_model = gr.Dropdown(
                label="Text Generation Model",
                choices=STANDARD_TEXT_MODELS,
                value="gpt-4o-mini", #"o1-preview-2024-09-12", #"gpt-4o-mini",
                info="Select the model to generate the dialogue text.",
            )
            reasoning_effort = gr.Dropdown(
                label="Reasoning effort (for reasoning models, e.g. o1, o3, o4)",
                choices=REASONING_EFFORTS,
                value="N/A", #standard selection for non-reasoning models
                info="Select reasoning effort used.",
            )
            
            audio_model = gr.Dropdown(
                label="Audio Generation Model",
                choices=STANDARD_AUDIO_MODELS,
                value="tts-1",
                info="Select the model to generate the audio.",
            )
            speaker_1_voice = gr.Dropdown(
                label="Speaker 1 Voice",
                choices=STANDARD_VOICES,
                value="alloy",
                info="Select the voice for Speaker 1.",
            )
            speaker_2_voice = gr.Dropdown(
                label="Speaker 2 Voice",
                choices=STANDARD_VOICES,
                value="echo",
                info="Select the voice for Speaker 2.",
            )
            speaker_1_instructions = gr.Textbox(
                label="Speaker 1 instructions",
                value="Speak in an emotive and friendly tone.",
                info="Speaker 1 instructions (used with gpt-4o-mini-tts only)",
                interactive=True,
            )

            speaker_2_instructions = gr.Textbox(
                label="Speaker 2 instructions",
                value="Speak in a friendly, but serious tone.",
                info="Speaker 2 instructions (used with gpt-4o-mini-tts only)",
                interactive=True,
            )
            
            api_base = gr.Textbox(
                label="Custom API Base",
                placeholder="Enter custom API base URL if using a custom/local model...",
                info="If you are using a custom or local model, provide the API base URL here, e.g.: http://localhost:8080/v1 for llama.cpp REST server.",
            )

            do_web_search = gr.Checkbox(
                label="Let the LLM search the web to complement the documents.",
                value=False,
                visible=False,  # Hidden until web search is properly implemented
                info="When enabled, the LLM will call the web search tool during its reasoning."
            )

        with gr.Column(scale=3):
            template_dropdown = gr.Dropdown(
                label="Instruction Template",
                choices=list(INSTRUCTION_TEMPLATES.keys()),
                value="podcast",
                info="Select the instruction template to use. You can also edit any of the fields for more tailored results.",
            )
            intro_instructions = gr.Textbox(
                label="Intro Instructions",
                lines=10,
                value=INSTRUCTION_TEMPLATES["podcast"]["intro"],
                info="Provide the introductory instructions for generating the dialogue.",
            )
            text_instructions = gr.Textbox(
                label="Standard Text Analysis Instructions",
                lines=10,
                placeholder="Enter text analysis instructions...",
                value=INSTRUCTION_TEMPLATES["podcast"]["text_instructions"],
                info="Provide the instructions for analyzing the raw data and text.",
            )
            scratch_pad_instructions = gr.Textbox(
                label="Scratch Pad Instructions",
                lines=15,
                value=INSTRUCTION_TEMPLATES["podcast"]["scratch_pad"],
                info="Provide the scratch pad instructions for brainstorming presentation/dialogue content.",
            )
            prelude_dialog = gr.Textbox(
                label="Prelude Dialog",
                lines=5,
                value=INSTRUCTION_TEMPLATES["podcast"]["prelude"],
                info="Provide the prelude instructions before the presentation/dialogue is developed.",
            )
            podcast_dialog_instructions = gr.Textbox(
                label="Podcast Dialog Instructions",
                lines=20,
                value=INSTRUCTION_TEMPLATES["podcast"]["dialog"],
                info="Provide the instructions for generating the presentation or podcast dialogue.",
            )

    audio_output = gr.Audio(label="Audio", format="mp3", interactive=False, autoplay=False)
    transcript_output = gr.Textbox(label="Transcript", lines=25, show_copy_button=True)
    original_text_output = gr.Textbox(label="Original Text", lines=10, visible=False)
    error_output = gr.Textbox(visible=False)  # Hidden textbox to store error message

    use_edited_transcript = gr.Checkbox(label="Use Edited Transcript (check if you want to make edits to the initially generated transcript)", value=False)
    edited_transcript = gr.Textbox(label="Edit Transcript Here. E.g., mark edits in the text with clear instructions. E.g., '[ADD DEFINITION OF MATERIOMICS]'.", lines=20, visible=False,
                                   show_copy_button=True, interactive=False)

    user_feedback = gr.Textbox(label="Provide Feedback or Notes", lines=10, #placeholder="Enter your feedback or notes here..."
                              )
    regenerate_btn = gr.Button("Regenerate Audio with Edits and Feedback")

    with gr.Accordion("Edit dialogue line‑by‑line", open=False) as editor_box:
        df_editor = gr.Dataframe(
            headers=["Speaker", "Line"],
            datatype=["str", "str"],
            wrap=True,
            interactive=True,
            row_count=(1, "dynamic"),
            col_count=(2, "fixed"),
        )

        save_btn   = gr.Button("Save edits")
        save_msg   = gr.Markdown()
        

    save_btn.click(
        fn=save_dialogue_edits,
        inputs=[df_editor, cached_dialogue],
        outputs=[cached_dialogue, transcript_output, save_msg],
    )
        
    rerender_btn = gr.Button("Re‑render with current voice settings (must have generated original LLM output)")
    
    rerender_btn.click(
        fn=render_audio_from_dialogue,
        inputs=[
            cached_dialogue,
            openai_api_key,
            audio_model,
            speaker_1_voice,
            speaker_2_voice,
            speaker_1_instructions,
            speaker_2_instructions,
        ],
        outputs=[audio_output, transcript_output],
    )


    
    # Function to update the interactive state of edited_transcript
    def update_edit_box(checkbox_value):
        return gr.update(interactive=checkbox_value, lines=20 if checkbox_value else 20, visible=True if checkbox_value else False)

    # Update the interactive state of edited_transcript when the checkbox is toggled
    use_edited_transcript.change(
        fn=update_edit_box,
        inputs=[use_edited_transcript],
        outputs=[edited_transcript]
    )
    # Update instruction fields when template is changed
    template_dropdown.change(
        fn=update_instructions,
        inputs=[template_dropdown],
        outputs=[intro_instructions, text_instructions, scratch_pad_instructions, prelude_dialog, podcast_dialog_instructions]
    )
    
    submit_btn.click(
        fn=validate_and_generate_audio,
        inputs=[
            files, openai_api_key, text_model, reasoning_effort, do_web_search, audio_model, 
            speaker_1_voice, speaker_2_voice, speaker_1_instructions, speaker_2_instructions,
            api_base,
            intro_instructions, text_instructions, scratch_pad_instructions, 
            prelude_dialog, podcast_dialog_instructions, 
            edited_transcript,   
            user_feedback,  
            
        ],
        outputs=[audio_output, transcript_output, original_text_output, error_output, cached_dialogue, ]
    ).then(
        fn=lambda audio, transcript, original_text, error: (
            transcript if transcript else "",
            error if error else None
        ),
        inputs=[audio_output, transcript_output, original_text_output, error_output],
        outputs=[edited_transcript, error_output]
    ).then(
        fn=lambda error: gr.Warning(error) if error else None,
        inputs=[error_output],
        outputs=[]
    ).then(              # fill spreadsheet editor
    fn=dialogue_to_df,
        inputs=[cached_dialogue],          
        outputs=[df_editor],
     )

    regenerate_btn.click(
        fn=lambda use_edit, edit, *args: validate_and_generate_audio(
            *args[:12],  # All inputs up to podcast_dialog_instructions
            edit if use_edit else "",  # Use edited transcript if checkbox is checked, otherwise empty string
            *args[12:]  # user_feedback and original_text_output
        ),
        inputs=[
            use_edited_transcript, edited_transcript,
            files, openai_api_key, text_model, reasoning_effort, do_web_search, audio_model, 
            speaker_1_voice, speaker_2_voice, speaker_1_instructions, speaker_2_instructions,
            api_base,
            intro_instructions, text_instructions, scratch_pad_instructions, 
            prelude_dialog, podcast_dialog_instructions,
            user_feedback, original_text_output
        ],
        outputs=[audio_output, transcript_output, original_text_output, error_output, cached_dialogue, ]
    ).then(
        fn=lambda audio, transcript, original_text, error: (
            transcript if transcript else "",
            error if error else None
        ),
        inputs=[audio_output, transcript_output, original_text_output, error_output],
        outputs=[edited_transcript, error_output]
    ).then(
        fn=lambda error: gr.Warning(error) if error else None,
        inputs=[error_output],
        outputs=[]
    ).then(                          # fill spreadsheet editor
    fn=dialogue_to_df,
        inputs=[cached_dialogue],          
        outputs=[df_editor],
     )

    with gr.Row():
        save_md_btn = gr.Button("Download Markdown of Dialogue")
        markdown_file_output = gr.File(label="Download .md file")
    
    save_md_btn.click(
        fn=save_dialogue_as_markdown,
        inputs=[cached_dialogue],
        outputs=[markdown_file_output],
    )

    # Add README content at the bottom
    gr.Markdown("---")  # Horizontal line to separate the interface from README
    gr.Markdown(read_readme())
    
# Enable queueing for better performance
demo.queue(max_size=20, default_concurrency_limit=32)

# Launch the Gradio app
if __name__ == "__main__":
    demo.launch(share=True)

#demo.launch()