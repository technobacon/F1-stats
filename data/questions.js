/**
 * F1 stats quiz data.
 *
 * Each category groups a set of multiple-choice questions. Questions are
 * intentionally stat-focused (records, counts, seasons) rather than opinion.
 * `answer` is the index into `options` of the correct choice.
 *
 * This is prototype data — curated by hand and verified against the
 * historical record up to the 2023 season. A future version can swap this
 * out for a live data source (e.g. the Ergast / Jolpica F1 API).
 */
window.QUIZ_DATA = {
  categories: [
    {
      id: "champions",
      name: "World Champions",
      icon: "🏆",
      blurb: "Drivers' titles, the legends who won them, and the records they set.",
      questions: [
        {
          q: "Who holds the record for the most Drivers' World Championships (7), shared with Michael Schumacher?",
          options: ["Sebastian Vettel", "Lewis Hamilton", "Alain Prost", "Ayrton Senna"],
          answer: 1,
          fact: "Lewis Hamilton matched Schumacher's 7 titles in 2020."
        },
        {
          q: "Who is the youngest Formula 1 World Champion in history?",
          options: ["Lewis Hamilton", "Fernando Alonso", "Sebastian Vettel", "Max Verstappen"],
          answer: 2,
          fact: "Sebastian Vettel won his first title in 2010 aged 23 years and 134 days."
        },
        {
          q: "How many consecutive Drivers' Championships did Sebastian Vettel win with Red Bull?",
          options: ["2", "3", "4", "5"],
          answer: 2,
          fact: "Vettel won four in a row from 2010 to 2013."
        },
        {
          q: "Which driver won the 1950 inaugural F1 World Championship?",
          options: ["Juan Manuel Fangio", "Giuseppe Farina", "Alberto Ascari", "Stirling Moss"],
          answer: 1,
          fact: "Giuseppe 'Nino' Farina took the very first title for Alfa Romeo."
        },
        {
          q: "Juan Manuel Fangio won five World Championships. With how many different constructors did he win them?",
          options: ["2", "3", "4", "5"],
          answer: 2,
          fact: "He won with Alfa Romeo, Mercedes, Ferrari and Maserati — four constructors."
        },
        {
          q: "Who was the first British driver to win the Formula 1 World Championship?",
          options: ["Stirling Moss", "Graham Hill", "Mike Hawthorn", "Jim Clark"],
          answer: 2,
          fact: "Mike Hawthorn won the 1958 title; Stirling Moss never won a championship."
        }
      ]
    },
    {
      id: "wins",
      name: "Wins & Podiums",
      icon: "🥇",
      blurb: "Race victories, podium streaks and the all-time win tallies.",
      questions: [
        {
          q: "Who holds the all-time record for most Grand Prix wins?",
          options: ["Michael Schumacher", "Lewis Hamilton", "Sebastian Vettel", "Ayrton Senna"],
          answer: 1,
          fact: "Hamilton overtook Schumacher's 91 wins and has over 100 victories."
        },
        {
          q: "In a single season, what is the record for most race wins (set in 2023)?",
          options: ["13", "15", "19", "21"],
          answer: 2,
          fact: "Max Verstappen won 19 of 22 races in 2023."
        },
        {
          q: "Which driver won 9 consecutive Grands Prix in 2013, an F1 record?",
          options: ["Lewis Hamilton", "Sebastian Vettel", "Nico Rosberg", "Max Verstappen"],
          answer: 1,
          fact: "Vettel's nine straight wins to end 2013 stood as the record until Verstappen matched and surpassed it in 2023."
        },
        {
          q: "Ayrton Senna scored how many career Grand Prix wins?",
          options: ["31", "41", "51", "25"],
          answer: 1,
          fact: "Senna won 41 races across his career."
        },
        {
          q: "Which constructor has the most race wins in F1 history?",
          options: ["McLaren", "Mercedes", "Ferrari", "Williams"],
          answer: 2,
          fact: "Ferrari, the oldest team on the grid, leads all constructors in wins."
        }
      ]
    },
    {
      id: "circuits",
      name: "Circuits & Tracks",
      icon: "🏁",
      blurb: "The corners, countries and calendars that make up the F1 season.",
      questions: [
        {
          q: "Which circuit is known as the 'Temple of Speed'?",
          options: ["Spa-Francorchamps", "Monza", "Silverstone", "Suzuka"],
          answer: 1,
          fact: "Monza in Italy is famous for its high speeds and long straights."
        },
        {
          q: "What is the most famous corner at the Circuit de Spa-Francorchamps?",
          options: ["Maggotts", "Eau Rouge", "130R", "Parabolica"],
          answer: 1,
          fact: "Eau Rouge (followed by Raidillon) is one of the most iconic corners in motorsport."
        },
        {
          q: "Which circuit hosts the Monaco Grand Prix's famous tight street layout?",
          options: ["Circuit de Monaco", "Marina Bay", "Baku City Circuit", "Albert Park"],
          answer: 0,
          fact: "The Circuit de Monaco winds through the streets of Monte Carlo."
        },
        {
          q: "Suzuka, host of the Japanese GP, is unusual for being which shape?",
          options: ["Oval", "Figure-eight", "Triangle", "Perfect circle"],
          answer: 1,
          fact: "Suzuka is the only figure-eight layout on the F1 calendar."
        },
        {
          q: "Which country hosts the Grand Prix held at Interlagos?",
          options: ["Argentina", "Mexico", "Brazil", "Portugal"],
          answer: 2,
          fact: "Autódromo José Carlos Pace (Interlagos) is in São Paulo, Brazil."
        }
      ]
    },
    {
      id: "records",
      name: "Records & Numbers",
      icon: "📊",
      blurb: "Pole positions, fastest laps and the stats that define greatness.",
      questions: [
        {
          q: "Who holds the record for the most career pole positions?",
          options: ["Ayrton Senna", "Michael Schumacher", "Lewis Hamilton", "Sebastian Vettel"],
          answer: 2,
          fact: "Hamilton has over 100 career pole positions, more than anyone in history."
        },
        {
          q: "How many points is a race win worth under the current (post-2010) scoring system?",
          options: ["10", "20", "25", "30"],
          answer: 2,
          fact: "A win is worth 25 points, with 18 for second and 15 for third."
        },
        {
          q: "What extra point can a driver earn during a Grand Prix (when finishing in the top 10)?",
          options: ["Pole position", "Fastest lap", "Most overtakes", "Leading lap one"],
          answer: 1,
          fact: "Fastest lap awards 1 bonus point if the driver finishes in the top 10."
        },
        {
          q: "Which team holds the record for most Constructors' Championships?",
          options: ["McLaren", "Williams", "Mercedes", "Ferrari"],
          answer: 3,
          fact: "Ferrari leads with the most Constructors' titles."
        },
        {
          q: "Approximately how many G-force can drivers experience under heavy braking?",
          options: ["1G", "3G", "5G", "10G"],
          answer: 2,
          fact: "Drivers regularly endure around 5G under braking and in high-speed corners."
        }
      ]
    }
  ]
};
