import os
from diffusers import StableDiffusionPipeline
from diffusers.schedulers import DDIMScheduler
import numpy as np
import wandb
import torch
from torch import autocast
from PIL import Image
from torchvision import transforms
from collections import Counter
from mnist_classifier.model import MnistClassifier
run =wandb.init(project="sinvadtestfitness_mnist")
img_size = 28 * 28
def disableNSFWFilter(pipe):
    """Disables the trigger happy nsfw filter. tread carefully"""
    def dummy(images, **kwargs):
        return images, [False] * len(images)
    pipe.safety_checker = dummy
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":16:8"
generator = torch.Generator(device = 'cuda')
torch.use_deterministic_algorithms(True)
def calculate_fitness(logit, label):

    expected_logit = logit[label]
    # Select the two best indices [perturbed logit]
    best_indices = np.argsort(-logit)[:2]
    best_index1, best_index2 = best_indices
    if best_index1 == label:
        best_but_not_expected = best_index2
    else:
        best_but_not_expected = best_index1
    new_logit = logit[best_but_not_expected]
    # Compute fitness value
    fitness = expected_logit - new_logit
    return fitness
classifier = MnistClassifier(img_size=img_size).to(device)
# Load pretrained model
classifier.load_state_dict(
    torch.load(
        "./mnist_classifier/MNIST_conv_classifier.pth",
        map_location=device,
   )
)
classifier.eval()
transform = transforms.Compose(
    [
        transforms.Resize((28, 28)),
        transforms.Grayscale(num_output_channels=1),  # This converts to grayscale
        transforms.ToTensor(),
        transforms.Normalize((0.5,),(0.5,)),
    ]
)

prompt = "A photo of three3 Number3" # prompt to dream about
proj_name = "test"
num_inference_steps = 20
width = 512
height = 512
init_perturbation = 0.002
best_left = 10
perturbation_size = 0.001
frame_index = 0
gen_steps = 500
pop_size = 25
fitness_scores = []
predicted_labels = []
all_img_lst = []
num_samples = 10
desired_label_index = 3
proj_path = "./evolution_mnist_sdsinvadkpp/"+proj_name+"_"
os.makedirs(proj_path, exist_ok=True)
os.makedirs(proj_path+'/Newresultjump', exist_ok=True)


print('Creating init image')
base_model_id = "runwayml/stable-diffusion-v1-5"
weights_path = "./Mnist_Lora_sdv1.5-000005.safetensors"

pipe = StableDiffusionPipeline.from_pretrained(
base_model_id, safety_checker=None).to(device)
pipe.load_lora_weights(weights_path)
pipe.scheduler = DDIMScheduler.from_config(pipe.scheduler.config)
#disableNSFWFilter(pipe)
#tokenizer = CLIPTokenizer.from_pretrained("openai/clip-vit-large-patch14")
pipe.unet.to(device)
pipe.vae.to(device)
pipe.text_encoder.to(device)
seed = 1024
for n in range(num_samples):
    generator = generator.manual_seed(seed)
    original_lv = torch.randn((1, pipe.unet.config.in_channels, height // 8, width // 8), device = device)
    with autocast("cuda"):
         init_img = pipe(prompt, num_inference_steps= num_inference_steps, latents=original_lv,)["images"][0]
    tensor_image = transform(init_img)
    tensor_image = tensor_image.unsqueeze(0).to(device)
    original_logit = classifier(tensor_image).squeeze().detach().cpu().numpy()
    original_label = np.argmax(original_logit).item()
    if original_label != desired_label_index:
        # If label doesn't match, skip the rest of the loop and continue with the next sample
        continue
    init_img_path = os.path.join(proj_path, f"matched_image_{n}_{original_label}.png")
    init_img.save(init_img_path)
    print(f"Saved image with matching label {original_label} at: {init_img_path}")

    init_pop = [
        original_lv + init_perturbation * torch.randn((4, height // 8, width // 8), device = device)
        for _ in range(pop_size)
    ]
   
    binom_sampler = torch.distributions.binomial.Binomial(
        probs=0.5 * torch.ones(original_lv.size(), device=device)
    )

    now_pop = init_pop
    prev_best = np.inf
    for i in range(gen_steps):

        indivs_lv = torch.cat(now_pop, dim=0).view(-1, 4, height // 8, width // 8)
        print(indivs_lv.shape)
        with torch.inference_mode(), torch.autocast("cuda"):
            perturb_img = pipe([prompt]*(pop_size),guidance_scale = 0.5, 
                num_inference_steps=num_inference_steps,generator = generator,
                latents=indivs_lv,
            )["images"]
           # all_img_lst.append(perturb_img)
        torch.cuda.empty_cache()
        tensor_image2 =torch.stack([transform(image) for image in perturb_img])
        tensor_image2 = tensor_image2.to(device)
        all_logits = classifier(tensor_image2).detach().cpu().numpy()
        perturb_label1 = np.argmax(all_logits).item()
        print(all_logits.shape)
        print(tensor_image2.shape)
        #os.makedirs(os.path.join(proj_path, 'generated_images'), exist_ok=True)  
        fitness_scores = [
            calculate_fitness(all_logits[k_idx], original_label)
            for k_idx in range(pop_size)
        ]
        print("print fitness",len(fitness_scores))
        # Perform selection
        selected_indices = sorted(range(len(fitness_scores)), key=lambda i: fitness_scores[i], reverse=True,
           )[-best_left:]
        now_best = np.min(fitness_scores)
        parent_pop = [now_pop[idx] for idx in selected_indices]
        print("now_best " + str(now_best) + " average_best " + str(np.mean(fitness_scores)))
        wandb.log({"ft_score":now_best})
        if now_best < 0:
           break
        elif now_best == prev_best:
            perturbation_size *= 2
        else:
            perturbation_size = init_perturbation
        k_pop = []
        print("Size of parent_pop:", len(parent_pop))
        # select k-idx for cross_over genes
        for k_idx in range(pop_size - best_left):
            mom_idx, pop_idx = np.random.choice(best_left, size=2, replace=False)
            print("mom_idx:", mom_idx, "pop_idx:", pop_idx)
            spl_idx = np.random.choice(4, size=1)[0]
            k_gene = torch.cat(
                [parent_pop[mom_idx][:, :spl_idx], parent_pop[pop_idx][:, spl_idx:]],
                dim=1,
            )  # crossover

            # Mutation
            diffs = (k_gene != original_lv).float()
            k_gene += (
                   perturbation_size * torch.randn(k_gene.size(), device = k_gene.device) * diffs
            )  # random adding noise only to diff places
            interp_mask = binom_sampler.sample()
            k_gene = interp_mask * original_lv + (1 - interp_mask) * k_gene
            k_pop.append(k_gene)
            print("Size of k_pop:", len(k_pop))

        # Combine parent_pop and k_pop for the next generation
        now_pop = parent_pop + k_pop
        prev_best = now_best
        
    mod_best = parent_pop[-1].view(1, 4, height // 8, width // 8)
    
    with torch.inference_mode():

       last_image_list = pipe(prompt,guidance_scale = 0, num_inference_steps= num_inference_steps, latents=mod_best)["images"]
    # After the loop, save the last image if it exists
    if last_image_list:
       last_image = last_image_list[0]
       tensor_image = transform(last_image)
       tensor_image = tensor_image.unsqueeze(0).to(device)
       perturb_logit = classifier(tensor_image).squeeze().detach().cpu().numpy()
       perturb_label = np.argmax(perturb_logit).item()
       predicted_labels.append(perturb_label)

    if last_image is not None:
       image_filename = f'image_{n}_iteration{i}_X{original_label}_Y{perturb_label}.png'
       last_image_path = os.path.join(proj_path, 'Newresultjump', image_filename)
       last_image.save(last_image_path)
       print(f"Last image saved at {last_image_path}")
    else:
       print("Error: No image was generated")


# After all images are processed (i.e., outside your main loop):
misclassified_count = sum(1 for predicted_label in predicted_labels if predicted_label != original_label)
total_images = len(predicted_labels)
misclassification_percentage = (misclassified_count / total_images) * 100

# Print or log the misclassification percentage
print(f"Misclassification Percentage: {misclassification_percentage}%")

# wandb to log metrics
wandb.log({"Misclassification Percentage": misclassification_percentage})