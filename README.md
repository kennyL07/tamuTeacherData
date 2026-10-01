
# tamuTeacherData
tamuTeacherData is a tool for students who want to choose the best teacher based on GPA and ratings from Rate My Professor(RMP)

 - Data on grade distributions and sections of teachers are collected from https://anex.us/grades/
 - Ratings are collected from Rate My Professor using school code 1003
 - All data is specific to the College-Station campus only (Blinn, and etc. are not included yet)
 ## Things to consider
- Scores are weighted 60% GPA and 40% RMP
	- If you value a RMP ratings over GPA, you may need to sort through the data through your own means
- There are some courses are taught by TA's who do not stay for very long. Data on these courses are not very useful for choosing professors
- New teachers usually do not have ratings and will show up as unranked at the bottom
- Scores calculated by me **do not reflect the teacher's performance** for the present year. Teacher's may improve and trend towards better practices that are not displayed by scores created from this program.

## Calculating Score
Scores are calculated through the following steps:

 1. Calculating the mean GPA and mean RMP in a department
 2. Calculating the Z-scores of GPA and RMP for individual teachers in the department
 3. Adjusting for grade inflation by comparing Z-scores of GPA and RMP
 4. Calculate the [Weighted Arithmetic Mean](https://en.wikipedia.org/wiki/Bayes_estimator#Example:_estimating_p_in_a_binomial_distribution) for each teacher based off the amount of votes that they have on RMP
 5. Score is 0.6 * adjustedGPA + 0.4 adjustedRMP
 6. Normalize the value to be between [0-10]

> Use this spreadsheet to find teachers for your classes
> https://docs.google.com/spreadsheets/d/1S9xo6-2zy1sTMCMKIUtvdSPaRbeIjutFYtP7CxHhUPw/edit?usp=sharing
